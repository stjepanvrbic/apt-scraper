"""Corresponds with listing agents and asks the user about listings, through the user's own connected accounts.

Commands:
  contact.py work                 print the contacts that need action, with listing facts and history
  contact.py send FILE            send {"listing", "channel": "email" or "imessage", "text", "subject" for a first email}
  contact.py submitted FILE       record a contact form you submitted {"listing", "text", "url"}
  contact.py ask FILE             ask the user through the gateway {"listing", "question", "field", "purpose"}
  contact.py tell FILE            tell the user something through the gateway {"listing", "text"}
  contact.py questions            print the questions the user has not answered
  contact.py answer FILE          record the user's answer {"question", "answer", "share"}
  contact.py agree FILE           record an agreed viewing and book it {"listing", "time", "details", "viewing", "user_confirmed"}
  contact.py cancel VIEWING       record that an agreed or booked viewing was canceled and remove its event
  contact.py done LISTING         mark the listing's replies handled without sending anything
  contact.py close LISTING        end the contact
  contact.py fail LISTING ERROR   mark the contact failed with the reason the gallery shows
  contact.py due                  record new replies from Gmail and iMessage, bring the calendar up to date with
                                  the viewings, then print the cron wake gate, closed while the account setup
                                  uses the browser

FILE is a JSON object in the scratch directory, deleted once it is used. Every send and every calendar write
first runs `connections.py require` for its channel and goes ahead only if it exits 0. An agreed viewing becomes
a calendar event, a moved one updates its event and a canceled one loses it, at the end of `agree` and `cancel`
and again on every `due` until the write works. Exit codes: 0 done,
1 rejected or failed and nothing was sent or recorded, 2 usage error, 3 the channel is not connected
and nothing was sent, 4 the listing waits for the user and nothing was sent or recorded.
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from email.utils import parseaddr
from pathlib import Path
from typing import Literal, TypedDict, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS / 'gallery' / 'scripts'))
sys.path.insert(0, str(SKILLS / 'account-connections' / 'scripts'))

import connections  # noqa: E402
from scratch import read_scratch_object  # noqa: E402
from shapes import Listing, ViewingAvailability, is_array, is_object, loads  # noqa: E402
from store import (VIEWING_MINUTES, WEEKDAYS, ContactRecord, ListingDetail, ListingStore, QuestionRecord, ViewingConflict,  # noqa: E402
                   ViewingRecord, store_path, viewing_time)

NOT_CONNECTED = 3
WAITING_FOR_USER = 4
COMMAND_TIMEOUT_SECONDS = 120
REQUIRE_TIMEOUT_SECONDS = 200
CHANNEL_LABELS = {'gmail': 'Gmail', 'imessage': 'iMessage'}
AgentField = Literal['name', 'email', 'phone', 'contact_form']
AGENT_DETAILS: tuple[tuple[AgentField, str], ...] = (('name', 'Listing agent'), ('email', 'Email'), ('phone', 'Phone'),
                                                      ('contact_form', 'Contact form'))
LISTING_FACTS = ('id', 'title', 'price', 'price_note', 'currency', 'rental_period', 'size', 'rooms', 'address',
                 'description', 'amenities', 'sources', 'status')


class SentMessage(TypedDict):
    """What a channel reports of a message it sent."""
    id: str
    threadId: str | None


class CalendarEvent(TypedDict):
    """What the conflict notice reads of an event Google Calendar lists."""
    summary: str
    start: str
    end: str


class EventTime(TypedDict):
    dateTime: str
    timeZone: str


class Event(TypedDict):
    """The calendar event of a viewing, as viewing_calendar.py saves it."""
    summary: str
    location: str
    description: str
    status: Literal['confirmed']
    start: EventTime
    end: EventTime


class SendResult(TypedDict):
    sent: bool
    message_id: str
    thread_id: str | None


class SubmittedResult(TypedDict):
    recorded: bool
    message_id: str


class AskResult(TypedDict):
    question: int


class ToldResult(TypedDict):
    told: bool


class AnswerResult(TypedDict):
    answered: int


class AgreeResult(TypedDict):
    viewing: str
    booked: bool
    calendar_error: str | None


class CancelResult(TypedDict):
    canceled: str
    calendar_error: str | None


class DueItem(TypedDict):
    """A contact that needs action, with what the agent needs to act on it."""
    reason: Literal['reply', 'answer', 'start']
    listing: dict[str, object]
    contact: ContactRecord | None
    correspondence: list[dict[str, object]]
    viewings: list[dict[str, object]]
    approvals: list[dict[str, object]]
    answers: list[dict[str, object]]


class WorkResult(TypedDict):
    due: list[DueItem]
    waiting: list[QuestionRecord]
    viewing_availability: ViewingAvailability | None


class NotConnected(Exception):
    pass


class WaitingForUser(Exception):
    pass


def require(channel: str) -> None:
    """The gate every send passes. Exit code 3 means the channel is not connected; any other nonzero
    code means the check itself failed, and the channel is not used either way."""
    script = SKILLS / 'account-connections' / 'scripts' / 'connections.py'
    process = execute([sys.executable, str(script), 'require', channel], f'The connection check for {channel}', REQUIRE_TIMEOUT_SECONDS)
    if process.returncode == connections.NOT_CONNECTED:
        raise NotConnected(process.stdout.strip())
    if process.returncode != 0:
        raise RuntimeError(f'The connection check for {channel} failed: {(process.stderr or process.stdout).strip()}')


def usable(channel: str) -> bool:
    try:
        require(channel)
    except NotConnected:
        return False
    return True


def execute(command: list[str], name: str, timeout: float = COMMAND_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    """Runs through the connection script's runner, which kills the whole process tree on a timeout, because
    launchers such as hermes.exe keep the output pipes open in a child."""
    runner = connections.Runner()
    runner.deadline = time.monotonic() + timeout
    try:
        return runner.run(command, name, timeout)
    except connections.CommandTimedOut as error:
        raise RuntimeError(str(error)) from None


def run(command: list[str], name: str) -> str:
    process = execute(command, name)
    if process.returncode != 0:
        lines = (process.stderr.strip() or process.stdout.strip() or f'exit code {process.returncode}').splitlines()
        raise RuntimeError(f'{name} failed: {lines[-1].strip()}')
    return process.stdout


def google_api(*arguments: str) -> object:
    script = Path(os.environ['HERMES_HOME']) / 'skills' / 'productivity' / 'google-workspace' / 'scripts' / 'google_api.py'
    return loads(run([sys.executable, str(script), *arguments], 'Google ' + ' '.join(arguments[:2])))


def as_object(value: object, name: str) -> dict[str, object]:
    """A JSON object a connector printed, whose fields are narrowed where they are read."""
    if not is_object(value):
        raise RuntimeError(f'{name} did not answer with an object.')
    return value


def as_objects(value: object, name: str) -> list[dict[str, object]]:
    if not is_array(value):
        raise RuntimeError(f'{name} did not answer with a list.')
    return [as_object(item, name) for item in value]


def required_text(item: Mapping[str, object], key: str, name: str) -> str:
    value = item.get(key)
    if not isinstance(value, str):
        raise RuntimeError(f'{name} did not report {key}.')
    return value


def optional_text(item: Mapping[str, object], key: str) -> str | None:
    """The field's text, or None when it is missing, null or not text."""
    value = item.get(key)
    return value if isinstance(value, str) else None


def tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f'The {name} command is not on PATH.')
    return path


def imsg(*arguments: str) -> str:
    return run([tool('imsg'), *arguments], 'imsg ' + arguments[0])


def imsg_records(*arguments: str) -> list[dict[str, object]]:
    """The records imsg prints one per line with --json."""
    output = imsg(*arguments, '--json')
    return [as_object(loads(line), 'imsg ' + arguments[0]) for line in output.splitlines() if line.strip()]


def chat_handles(chat: Mapping[str, object]) -> list[str]:
    """The identifier and the participants of a chat imsg listed."""
    participants = chat.get('participants')
    handles = [chat.get('identifier'), *(participants if is_array(participants) else ())]
    return [handle for handle in handles if isinstance(handle, str)]


def same_handle(first: str, second: str) -> bool:
    digits = [re.sub(r'\D', '', value or '') for value in (first, second)]
    if min(map(len, digits)) >= 7:
        return digits[0][-10:] == digits[1][-10:]
    return bool(first) and first.strip().lower() == (second or '').strip().lower()


def label(listing: Listing) -> str:
    return ', '.join(part for part in (listing.get('title'), listing.get('address')) if part) or listing['id']


def contact_of(store: ListingStore, listing_id: str) -> tuple[ListingDetail, ContactRecord]:
    listing = store.listing(listing_id)
    contact = listing['contact']
    if not contact:
        raise KeyError(f'No contact request for {listing_id}.')
    return listing, contact


def hold_for_user(store: ListingStore, listing_id: str) -> None:
    if store.open_questions(listing_id):
        raise WaitingForUser(f'{listing_id} is waiting for the user to answer a question. Nothing was sent.')


def tell_user(text: str) -> dict[str, object]:
    """Sends to the gateway platform whose check passed, through a file so the text reaches Hermes unchanged."""
    require('gateway')
    platform = connections.load_status()['gateway'].get('platform')
    if platform is None:
        raise KeyError('platform')
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.txt', delete=False) as file:
        _ = file.write(text)
    try:
        result = loads(run([tool('hermes'), 'send', '--to', platform, '--json', '--file', file.name], 'hermes send'))
    finally:
        os.unlink(file.name)
    if not is_object(result):
        raise RuntimeError(f'The message to the user was not delivered: {result}')
    if result.get('error') or result.get('skipped') or not result.get('success'):
        raise RuntimeError(f'The message to the user was not delivered: {result.get("error") or result.get("note") or result}')
    return result


def sent_message(value: object) -> SentMessage:
    """What Gmail reports of the message it sent."""
    message = as_object(value, 'Gmail')
    return {'id': required_text(message, 'id', 'Gmail'), 'threadId': optional_text(message, 'threadId')}


def send_email(listing: ListingDetail, contact: ContactRecord, text: str, subject: object) -> tuple[SentMessage, str | None]:
    history = listing['correspondence']
    incoming = [item for item in history if item['channel'] == 'email' and item['direction'] == 'incoming']
    outgoing = [item for item in history if item['channel'] == 'email' and item['direction'] == 'outgoing']
    if incoming:
        return sent_message(google_api('gmail', 'reply', incoming[-1]['external_message_id'], '--body', text)), None
    address = contact['agent'].get('email')
    if not address:
        raise ValueError('The listing agent has no email address.')
    if outgoing:
        thread_id, first_subject = outgoing[-1]['external_thread_id'], contact['subject']
        if thread_id is None or first_subject is None:
            raise ValueError('The first email of this contact has no thread or no subject to follow up in.')
        return sent_message(google_api('gmail', 'send', '--to', address, '--subject', 'Re: ' + first_subject, '--body', text,
                                       '--thread-id', thread_id)), None
    if not isinstance(subject, str) or not subject:
        raise ValueError('A first email needs a subject.')
    return sent_message(google_api('gmail', 'send', '--to', address, '--subject', subject, '--body', text)), subject


def send_text(contact: ContactRecord, text: str) -> SentMessage:
    handle = contact['agent'].get('phone')
    if not handle:
        raise ValueError('The listing agent has no phone number.')
    _ = imsg('send', '--to', handle, '--text', text)
    for chat in imsg_records('chats', '--limit', '200'):
        if any(same_handle(handle, participant) for participant in chat_handles(chat)):
            for message in imsg_records('history', '--chat-id', str(chat['id']), '--limit', '10'):
                if message.get('is_from_me') and message.get('text') == text:
                    return {'id': required_text(message, 'guid', 'imsg history'),
                            'threadId': optional_text(message, 'chat_guid') or optional_text(chat, 'guid')}
    return {'id': f'imessage:{uuid4()}', 'threadId': None}


def command_send(store: ListingStore, value: dict[str, object]) -> SendResult:
    listing_id, channel, text = value.get('listing'), value.get('channel'), value.get('text')
    if not isinstance(listing_id, str) or channel not in ('email', 'imessage') or not isinstance(text, str) or not text.strip():
        raise ValueError('send needs listing, channel email or imessage, and text.')
    listing, contact = contact_of(store, listing_id)
    if contact['status'] == 'closed':
        raise ValueError(f'The contact for {listing_id} is closed.')
    hold_for_user(store, listing_id)
    try:
        require('gmail' if channel == 'email' else 'imessage')
    except NotConnected as error:
        store.set_contact(listing_id, 'failed', error=f'Could not write to the listing agent. {error}')
        raise
    if channel == 'email':
        sent, subject = send_email(listing, contact, text, value.get('subject'))
    else:
        sent, subject = send_text(contact, text), None
    _ = store.correspond(listing_id, 'outgoing', channel, text, external_message_id=sent['id'], external_thread_id=sent['threadId'] or None)
    store.set_contact(listing_id, 'contacted', channel=channel, subject=subject)
    return {'sent': True, 'message_id': sent['id'], 'thread_id': sent['threadId'] or None}


def command_submitted(store: ListingStore, value: dict[str, object]) -> SubmittedResult:
    listing_id, text, url = value.get('listing'), value.get('text'), value.get('url')
    if not isinstance(listing_id, str) or not isinstance(text, str) or not text.strip() or not isinstance(url, str):
        raise ValueError('submitted needs listing, the text you submitted and the form URL.')
    _ = contact_of(store, listing_id)
    identifier = f'form:{uuid4()}'
    _ = store.correspond(listing_id, 'outgoing', 'contact_form', text, external_message_id=identifier, external_thread_id=url)
    store.set_contact(listing_id, 'contacted', channel='contact_form')
    return {'recorded': True, 'message_id': identifier}


def command_ask(store: ListingStore, value: dict[str, object]) -> AskResult:
    listing_id, question = value.get('listing'), value.get('question')
    if not isinstance(listing_id, str) or not isinstance(question, str) or not question.strip():
        raise ValueError('ask needs listing and question.')
    listing, _ = contact_of(store, listing_id)
    link = listing['sources'][0]['url']
    try:
        result = tell_user(f'Question about {label(listing)} ({link}):\n\n{question.strip()}\n\n'
                           + 'Reply here, and I will continue with the listing agent.')
    except NotConnected as error:
        store.set_contact(listing_id, 'failed', error=f'A question for you could not be sent. {error} The question: {question.strip()}')
        raise
    message_id = result.get('message_id')
    return {'question': store.ask_user(listing_id, question, field=value.get('field'), purpose=value.get('purpose'),
                                       external_message_id=str(message_id) if message_id is not None else None)}


def command_tell(store: ListingStore, value: dict[str, object]) -> ToldResult:
    text, listing_id = value.get('text'), value.get('listing')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('tell needs text.')
    if listing_id:
        if not isinstance(listing_id, str):
            raise ValueError('tell names the listing by its id.')
        listing, _ = contact_of(store, listing_id)
        text = f'{label(listing)} ({listing["sources"][0]["url"]}):\n\n{text.strip()}'
    _ = tell_user(text)
    return {'told': True}


def command_answer(store: ListingStore, value: dict[str, object]) -> AnswerResult:
    question, answer = value.get('question'), value.get('answer')
    if type(question) is not int or not isinstance(answer, str):
        raise ValueError('answer needs the question number and the answer.')
    store.answer_question(question, answer, share=value.get('share'))
    return {'answered': question}


def within_availability(availability: ViewingAvailability, start: datetime) -> bool:
    zone = availability.get('timezone')
    if zone is None:
        raise KeyError('timezone')
    local = start.astimezone(ZoneInfo(zone))
    end = local + timedelta(minutes=VIEWING_MINUTES)
    if end.date() != local.date():
        return False
    for period in availability.get('unavailable', []):
        if date.fromisoformat(period['from']) <= local.date() <= date.fromisoformat(period['to']):
            return False
    day = WEEKDAYS[local.weekday()]
    return any(window['day'] == day and window['start'] <= local.strftime('%H:%M') and end.strftime('%H:%M') <= window['end']
               for window in availability.get('weekly', []))


def calendar_conflicts(store: ListingStore, start: datetime) -> list[CalendarEvent]:
    """All-day events are left out: the saved availability's unavailable dates are where whole days off live. Events
    of agreed and booked viewings are left out too: the store's overlap rule decides about those, with its own answer,
    and a moved viewing must not clash with its own event."""
    end = start + timedelta(minutes=VIEWING_MINUTES)
    viewings = {event_id for viewing in store.scheduled_viewings()
                for event_id in (viewing['calendar_event_id'], created_event_id(viewing['id']))}
    events = as_objects(google_api('calendar', 'list', '--start', start.isoformat(), '--end', end.isoformat(), '--max', '10'),
                        'Google Calendar')
    conflicts: list[CalendarEvent] = []
    for event in events:
        begins, summary = optional_text(event, 'start') or '', optional_text(event, 'summary')
        if 'T' in begins and event.get('status') != 'cancelled' and event.get('id') not in viewings:
            conflicts.append({'summary': 'an event' if summary is None else summary, 'start': begins,
                              'end': required_text(event, 'end', 'Google Calendar')})
    return conflicts


def created_event_id(viewing_id: str) -> str:
    """The identifier of the event this script creates for a viewing. Calendar event identifiers allow only lowercase
    a to v and digits, 5 to 1024 characters (Calendar API, Events resource `id`), which a UUID without hyphens satisfies."""
    return viewing_id.replace('-', '')


def local_start(store: ListingStore, viewing: ViewingRecord) -> tuple[datetime, str]:
    availability = store.settings().get('viewing_availability')
    zone = availability.get('timezone', 'UTC') if availability else 'UTC'
    return datetime.fromisoformat(viewing['time']).astimezone(ZoneInfo(zone)), zone


def viewing_event(store: ListingStore, viewing: ViewingRecord) -> Event:
    listing = store.listing(viewing['listing_id'])
    contact = listing['contact']
    agent = contact['agent'] if contact else listing['agent']
    start, zone = local_start(store, viewing)
    lines = [label(listing), *(f'{source["label"]}: {source["url"]}' for source in listing['sources']), '',
             *(f'{name}: {value}' for key, name in AGENT_DETAILS if (value := agent.get(key)))]
    if viewing['details'].strip():
        lines += ['', viewing['details'].strip()]
    return {'summary': f'Apartment viewing: {listing.get("address") or label(listing)}', 'location': listing.get('address') or '',
            'description': '\n'.join(lines), 'status': 'confirmed',
            'start': {'dateTime': start.isoformat(), 'timeZone': zone},
            'end': {'dateTime': (start + timedelta(minutes=VIEWING_MINUTES)).isoformat(), 'timeZone': zone}}


def write_calendar(*arguments: str) -> object:
    script = Path(__file__).resolve().with_name('viewing_calendar.py')
    return loads(run([sys.executable, str(script), *arguments], 'Google Calendar ' + arguments[0]))


def save_event(store: ListingStore, viewing: ViewingRecord) -> bool:
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.json', delete=False) as file:
        json.dump({'event_id': viewing['calendar_event_id'], 'new_id': created_event_id(viewing['id']),
                   'event': viewing_event(store, viewing)}, file, ensure_ascii=False)
    try:
        saved = as_object(write_calendar('save', file.name), 'Google Calendar')
    finally:
        os.unlink(file.name)
    return store.record_booking(viewing['id'], viewing['time'], viewing['details'], required_text(saved, 'id', 'Google Calendar'))


def remove_event(store: ListingStore, viewing: ViewingRecord) -> bool:
    event_id = viewing['calendar_event_id']
    if event_id is None:
        raise ValueError(f'The viewing {viewing["id"]} has no calendar event to remove.')
    _ = write_calendar('delete', event_id)
    _ = store.record_event_removed(viewing['id'], event_id)
    return False


def sync_calendar(store: ListingStore) -> tuple[set[str], dict[str, tuple[ViewingRecord, str]]]:
    """Returns the ids of the viewings it booked and, per viewing whose write failed, the viewing and the error. A
    failed write does not hold up the others. A calendar that is not connected fails every remaining write unsent."""
    booked: set[str] = set()
    failures: dict[str, tuple[ViewingRecord, str]] = {}
    disconnected: str | None = None
    writes: list[tuple[Callable[[ListingStore, ViewingRecord], bool], ViewingRecord]]
    writes = [(save_event, viewing) for viewing in store.agreed_viewings()]
    writes += [(remove_event, viewing) for viewing in store.canceled_viewing_events()]
    for write, viewing in writes:
        if disconnected:
            failures[viewing['id']] = (viewing, disconnected)
            continue
        try:
            require('calendar')
            if write(store, viewing):
                booked.add(viewing['id'])
        except NotConnected as error:
            disconnected = str(error)
            failures[viewing['id']] = (viewing, disconnected)
        except (RuntimeError, ValueError, KeyError, OSError, sqlite3.Error) as error:
            failures[viewing['id']] = (viewing, error_text(error))
    return booked, failures


def error_text(error: Exception) -> str:
    """A KeyError's message is its key, which str() would quote."""
    return str(error.args[0] if isinstance(error, KeyError) else error)


def sync_after(store: ListingStore, viewing_id: str) -> tuple[bool, str | None]:
    """Runs after a command has committed its change, so a failure is reported in its output and never fails the
    command, which would otherwise read as "nothing was recorded"."""
    try:
        booked, failures = sync_calendar(store)
    except sqlite3.Error as error:
        return False, f'The calendar was not updated, because the store could not be read: {error}'
    return viewing_id in booked, failures[viewing_id][1] if viewing_id in failures else None


def calendar_notices(store: ListingStore, failures: Mapping[str, tuple[ViewingRecord, str]]) -> dict[str, str]:
    """One notice per distinct error, naming the viewings it holds up."""
    viewings_by_error: dict[str, list[ViewingRecord]] = {}
    for viewing, error in failures.values():
        viewings_by_error.setdefault(error, []).append(viewing)
    notices: dict[str, str] = {}
    for error, viewings in viewings_by_error.items():
        names = ', '.join(f'{label(store.listing(viewing["listing_id"]))} on {local_start(store, viewing)[0]:%a %d %b at %H:%M}'
                          for viewing in viewings)
        notices['calendar: ' + error] = (f'I could not update your Google Calendar for the viewing of {names}: {error} '
                                         + 'I will keep trying every few minutes.')
    return notices


def command_agree(store: ListingStore, value: dict[str, object]) -> AgreeResult:
    listing_id, time, details = value.get('listing'), value.get('time'), value.get('details', '')
    viewing = value.get('viewing')
    if not isinstance(listing_id, str) or viewing is not None and not isinstance(viewing, str):
        raise ValueError('agree needs listing and time, and the id of the viewing it moves.')
    _ = contact_of(store, listing_id)
    start = datetime.fromisoformat(viewing_time(time))
    if value.get('user_confirmed') is not True:
        availability = store.settings().get('viewing_availability')
        if not availability or not within_availability(availability, start):
            raise WaitingForUser(f'{time} is outside the user\'s viewing availability. Ask the user before agreeing to it.')
        require('calendar')
        conflicts = calendar_conflicts(store, start)
        if conflicts:
            names = ', '.join(f'{event["summary"]} ({event["start"]} to {event["end"]})' for event in conflicts)
            raise WaitingForUser(f'The calendar has {names} at {time}. Ask the user before agreeing to it.')
    try:
        viewing_id = store.agree_viewing(listing_id, time, details, viewing_id=viewing)
    except ViewingConflict as conflict:
        raise WaitingForUser(f'{conflict} Propose another time.') from None
    booked, error = sync_after(store, viewing_id)
    return {'viewing': viewing_id, 'booked': booked, 'calendar_error': error}


def command_work(store: ListingStore) -> WorkResult:
    due: list[DueItem] = []
    for item in store.due_contacts():
        listing = store.listing(item['listing_id'])
        due.append({
            'reason': item['reason'],
            'listing': {key: listing.get(key) for key in LISTING_FACTS},
            'contact': listing['contact'],
            'correspondence': [{key: message[key] for key in ('direction', 'channel', 'text', 'created_at')}
                               for message in listing['correspondence']],
            'viewings': [{key: viewing[key] for key in ('id', 'time', 'details', 'status')} for viewing in listing['viewings']],
            'approvals': [{key: approval[key] for key in ('id', 'field', 'value', 'purpose')} for approval in store.approvals(item['listing_id'])],
            'answers': [{key: answer[key] for key in ('id', 'question', 'field', 'purpose', 'answer', 'answered_at')}
                        for answer in store.unhandled_answers(item['listing_id'])],
        })
    return {'due': due, 'waiting': store.open_questions(), 'viewing_availability': store.settings().get('viewing_availability')}


def record(store: ListingStore, listing_id: str, channel: str, message_id: str, thread_id: str | None, text: str | None) -> None:
    if not store.has_message(listing_id, channel, message_id):
        _ = store.correspond(listing_id, 'incoming', channel, (text or '').strip()[:20000] or '(The message has no text.)',
                             external_message_id=message_id, external_thread_id=thread_id)


def only(listing_ids: list[str]) -> str | None:
    """A reply outside a known thread is filed only when exactly one open contact uses that address or number."""
    return listing_ids[0] if len(listing_ids) == 1 else None


def poll_gmail(store: ListingStore, contacts: list[ContactRecord]) -> None:
    owners = store.thread_owners('email')
    senders: dict[str, list[str]] = {}
    queries: set[str] = set()
    for contact in contacts:
        address = (contact['agent'].get('email') or '').lower()
        terms = [f'from:{address}'] if address else []
        if address:
            senders.setdefault(address, []).append(contact['listing_id'])
        if contact['subject']:
            terms.append('subject:"{}"'.format(contact['subject'].replace('"', '')))
        if terms:
            since = (datetime.fromisoformat(contact['requested_at']) - timedelta(days=1)).strftime('%Y/%m/%d')
            queries.add(f'-from:me after:{since} ' + (terms[0] if len(terms) == 1 else '{' + ' '.join(terms) + '}'))
    seen: set[str] = set()
    for query in sorted(queries):
        for message in as_objects(google_api('gmail', 'search', query, '--max', '25'), 'Gmail'):
            message_id = required_text(message, 'id', 'Gmail')
            if message_id in seen:
                continue
            seen.add(message_id)
            thread_id = optional_text(message, 'threadId')
            sender = parseaddr(optional_text(message, 'from') or '')[1].lower()
            listing_id = (thread_id and owners.get(thread_id)) or only(senders.get(sender, []))
            if listing_id and not store.has_message(listing_id, 'email', message_id):
                full = as_object(google_api('gmail', 'get', message_id), 'Gmail')
                record(store, listing_id, 'email', message_id, thread_id, optional_text(full, 'body') or optional_text(full, 'snippet'))


def poll_imessage(store: ListingStore, contacts: list[ContactRecord]) -> None:
    owners = store.thread_owners('imessage')
    requested = {contact['listing_id']: contact['requested_at'] for contact in contacts}
    phones = [(contact['listing_id'], phone) for contact in contacts if (phone := contact['agent'].get('phone'))]
    if not phones and not owners:
        return
    for chat in imsg_records('chats', '--limit', '200'):
        handles, guid = chat_handles(chat), optional_text(chat, 'guid')
        listing_id = (guid and owners.get(guid)) or only([listing for listing, phone in phones
                                                           if any(same_handle(phone, handle) for handle in handles)])
        if listing_id not in requested:
            continue
        for message in imsg_records('history', '--chat-id', str(chat['id']), '--start', requested[listing_id], '--limit', '50'):
            text = optional_text(message, 'text')
            if not message.get('is_from_me') and text:
                record(store, listing_id, 'imessage', required_text(message, 'guid', 'imsg history'),
                       optional_text(message, 'chat_guid') or guid, text)


def notice_state_path() -> Path:
    return Path(os.environ['HERMES_HOME']) / 'apt-scraper' / 'contact-notices.json'


def report_errors(notices: Mapping[str, str]) -> None:
    """Tells the user once about each new error of a background step, so an outage does not message them every tick.
    `notices` maps each failing step to what the user is told."""
    path = notice_state_path()
    reported: dict[str, str]
    try:
        reported = cast(dict[str, str], json.loads(path.read_text(encoding='utf-8')))
    except (FileNotFoundError, json.JSONDecodeError):
        reported = {}
    for step, notice in notices.items():
        if reported.get(step) != notice:
            try:
                _ = tell_user(notice)
            except (NotConnected, RuntimeError, OSError) as failure:
                print(f'Telling the user "{notice}" did not work: {failure}', file=sys.stderr)
                continue
            reported[step] = notice
    current = {step: notice for step, notice in reported.items() if step in notices}
    if current:
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(json.dumps(current), encoding='utf-8')
    else:
        path.unlink(missing_ok=True)


def command_due(store: ListingStore) -> int:
    contacts = store.active_contacts()
    notices: dict[str, str] = {}
    for channel, poll in (('gmail', poll_gmail), ('imessage', poll_imessage)):
        if not contacts:
            continue
        try:
            if usable(channel):
                poll(store, contacts)
        except (RuntimeError, ValueError, KeyError, OSError) as error:
            notices[channel] = (f'I could not check {CHANNEL_LABELS[channel]} for replies from listing agents: {error} '
                                + 'I will keep trying every few minutes.')
    notices.update(calendar_notices(store, sync_calendar(store)[1]))
    report_errors(notices)
    due = store.due_contacts()
    starts = sum(item['reason'] == 'start' for item in due)
    replies = len(due) - starts
    if starts:
        print(f'{starts} new contact request{"" if starts == 1 else "s"}')
    if replies:
        print(f'{replies} repl{"y" if replies == 1 else "ies"} to answer')
    # The account setup's browser carries the user's sign-ins in a visible window, and this job's contact forms
    # would get it too, so the job waits while a setup uses it, and the setup waits for a run to end.
    try:
        wake = connections.contact_job_gate(bool(due))
    except (RuntimeError, OSError, connections.CommandTimedOut, connections.OutOfTime) as error:
        print(f"The account setup's browser could not be checked: {error}")
        wake = False
    else:
        if due and not wake:
            print('Waiting while the account setup uses the browser.')
    print(json.dumps({'wakeAgent': wake}))
    return 0


FILE_COMMANDS: dict[str, Callable[[ListingStore, dict[str, object]], Mapping[str, object]]] = {
    'send': command_send, 'submitted': command_submitted, 'ask': command_ask, 'tell': command_tell,
    'answer': command_answer, 'agree': command_agree}


class Arguments(argparse.Namespace):
    command: str = ''
    file: Path = Path()
    listing: str = ''
    viewing: str = ''
    error: str = ''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Correspond with listing agents and ask the user about listings.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('work', 'questions', 'due'):
        _ = commands.add_parser(name)
    for name in FILE_COMMANDS:
        _ = commands.add_parser(name).add_argument('file', type=Path)
    for name in ('done', 'close'):
        _ = commands.add_parser(name).add_argument('listing')
    _ = commands.add_parser('cancel').add_argument('viewing')
    failing = commands.add_parser('fail')
    _ = failing.add_argument('listing')
    _ = failing.add_argument('error')
    arguments = parser.parse_args(argv, namespace=Arguments())
    store = ListingStore(store_path())
    if arguments.command == 'due':
        return command_due(store)
    if arguments.command == 'work':
        result = command_work(store)
    elif arguments.command == 'questions':
        result = store.open_questions()
    elif arguments.command == 'done':
        store.mark_handled(arguments.listing)
        result = {'handled': arguments.listing}
    elif arguments.command == 'cancel':
        store.cancel_viewing(arguments.viewing, possible_event_id=created_event_id(arguments.viewing))
        result = CancelResult(canceled=arguments.viewing, calendar_error=sync_after(store, arguments.viewing)[1])
    elif arguments.command == 'close':
        store.set_contact(arguments.listing, 'closed')
        result = {'closed': arguments.listing}
    elif arguments.command == 'fail':
        store.set_contact(arguments.listing, 'failed', error=arguments.error)
        result = {'failed': arguments.listing}
    else:
        file, value = read_scratch_object(arguments.file, arguments.command.capitalize())
        result = FILE_COMMANDS[arguments.command](store, value)
        file.unlink()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except NotConnected as error:
        print(error)
        sys.exit(NOT_CONNECTED)
    except WaitingForUser as error:
        print(error, file=sys.stderr)
        sys.exit(WAITING_FOR_USER)
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(error_text(error), file=sys.stderr)
        sys.exit(1)
