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
                                  the viewings, then print the cron wake gate

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
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from email.utils import parseaddr
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS / 'gallery' / 'scripts'))
sys.path.insert(0, str(SKILLS / 'account-connections' / 'scripts'))

import connections  # noqa: E402
from scratch import read_scratch_object  # noqa: E402
from store import VIEWING_MINUTES, WEEKDAYS, ListingStore, ViewingConflict, store_path, viewing_time  # noqa: E402

NOT_CONNECTED = 3
WAITING_FOR_USER = 4
COMMAND_TIMEOUT_SECONDS = 120
REQUIRE_TIMEOUT_SECONDS = 200
CHANNEL_LABELS = {'gmail': 'Gmail', 'imessage': 'iMessage'}
AGENT_DETAILS = (('name', 'Listing agent'), ('email', 'Email'), ('phone', 'Phone'), ('contact_form', 'Contact form'))
LISTING_FACTS = ('id', 'title', 'price', 'price_note', 'currency', 'rental_period', 'size', 'rooms', 'address',
                 'description', 'amenities', 'sources', 'status')


class NotConnected(Exception):
    pass


class WaitingForUser(Exception):
    pass


def require(channel):
    """The gate every send passes. Exit code 3 means the channel is not connected; any other nonzero
    code means the check itself failed, and the channel is not used either way."""
    script = SKILLS / 'account-connections' / 'scripts' / 'connections.py'
    process = execute([sys.executable, str(script), 'require', channel], f'The connection check for {channel}', REQUIRE_TIMEOUT_SECONDS)
    if process.returncode == connections.NOT_CONNECTED:
        raise NotConnected(process.stdout.strip())
    if process.returncode != 0:
        raise RuntimeError(f'The connection check for {channel} failed: {(process.stderr or process.stdout).strip()}')


def usable(channel):
    try:
        require(channel)
    except NotConnected:
        return False
    return True


def execute(command, name, timeout=COMMAND_TIMEOUT_SECONDS):
    """Runs through the connection script's runner, which kills the whole process tree on a timeout, because
    launchers such as hermes.exe keep the output pipes open in a child."""
    runner = connections.Runner()
    runner.deadline = time.monotonic() + timeout
    try:
        return runner.run(command, name, timeout)
    except connections.CommandTimedOut as error:
        raise RuntimeError(str(error)) from None


def run(command, name):
    process = execute(command, name)
    if process.returncode != 0:
        lines = (process.stderr.strip() or process.stdout.strip() or f'exit code {process.returncode}').splitlines()
        raise RuntimeError(f'{name} failed: {lines[-1].strip()}')
    return process.stdout


def google_api(*arguments):
    script = Path(os.environ['HERMES_HOME']) / 'skills' / 'productivity' / 'google-workspace' / 'scripts' / 'google_api.py'
    return json.loads(run([sys.executable, str(script), *arguments], 'Google ' + ' '.join(arguments[:2])))


def tool(name):
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f'The {name} command is not on PATH.')
    return path


def imsg(*arguments):
    output = run([tool('imsg'), *arguments], 'imsg ' + arguments[0])
    return [json.loads(line) for line in output.splitlines() if line.strip()] if '--json' in arguments else output


def same_handle(first, second):
    digits = [re.sub(r'\D', '', value or '') for value in (first, second)]
    if min(map(len, digits)) >= 7:
        return digits[0][-10:] == digits[1][-10:]
    return bool(first) and first.strip().lower() == (second or '').strip().lower()


def label(listing):
    return ', '.join(part for part in (listing.get('title'), listing.get('address')) if part) or listing['id']


def contact_of(store, listing_id):
    listing = store.listing(listing_id)
    if not listing['contact']:
        raise KeyError(f'No contact request for {listing_id}.')
    return listing


def hold_for_user(store, listing_id):
    if store.open_questions(listing_id):
        raise WaitingForUser(f'{listing_id} is waiting for the user to answer a question. Nothing was sent.')


def tell_user(text):
    """Sends to the gateway platform whose check passed, through a file so the text reaches Hermes unchanged."""
    require('gateway')
    platform = connections.load_status()['gateway']['platform']
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.txt', delete=False) as file:
        file.write(text)
    try:
        result = json.loads(run([tool('hermes'), 'send', '--to', platform, '--json', '--file', file.name], 'hermes send'))
    finally:
        os.unlink(file.name)
    if result.get('error') or result.get('skipped') or not result.get('success'):
        raise RuntimeError(f'The message to the user was not delivered: {result.get("error") or result.get("note") or result}')
    return result


def send_email(listing, text, subject):
    contact = listing['contact']
    history = listing['correspondence']
    incoming = [item for item in history if item['channel'] == 'email' and item['direction'] == 'incoming']
    outgoing = [item for item in history if item['channel'] == 'email' and item['direction'] == 'outgoing']
    if incoming:
        return google_api('gmail', 'reply', incoming[-1]['external_message_id'], '--body', text), None
    address = contact['agent'].get('email')
    if not address:
        raise ValueError('The listing agent has no email address.')
    if outgoing:
        return google_api('gmail', 'send', '--to', address, '--subject', 'Re: ' + contact['subject'], '--body', text,
                          '--thread-id', outgoing[-1]['external_thread_id']), None
    if not subject:
        raise ValueError('A first email needs a subject.')
    return google_api('gmail', 'send', '--to', address, '--subject', subject, '--body', text), subject


def send_text(listing, text):
    handle = listing['contact']['agent'].get('phone')
    if not handle:
        raise ValueError('The listing agent has no phone number.')
    imsg('send', '--to', handle, '--text', text)
    for chat in imsg('chats', '--limit', '200', '--json'):
        if any(same_handle(handle, participant) for participant in [chat.get('identifier'), *chat.get('participants', [])]):
            for message in imsg('history', '--chat-id', str(chat['id']), '--limit', '10', '--json'):
                if message.get('is_from_me') and message.get('text') == text:
                    return {'id': message['guid'], 'threadId': message.get('chat_guid') or chat.get('guid')}
    return {'id': f'imessage:{uuid4()}', 'threadId': None}


def command_send(store, value):
    listing_id, channel, text = value.get('listing'), value.get('channel'), value.get('text')
    if channel not in ('email', 'imessage') or not isinstance(text, str) or not text.strip():
        raise ValueError('send needs listing, channel email or imessage, and text.')
    listing = contact_of(store, listing_id)
    if listing['contact']['status'] == 'closed':
        raise ValueError(f'The contact for {listing_id} is closed.')
    hold_for_user(store, listing_id)
    try:
        require('gmail' if channel == 'email' else 'imessage')
    except NotConnected as error:
        store.set_contact(listing_id, 'failed', error=f'Could not write to the listing agent. {error}')
        raise
    if channel == 'email':
        sent, subject = send_email(listing, text, value.get('subject'))
    else:
        sent, subject = send_text(listing, text), None
    store.correspond(listing_id, 'outgoing', channel, text, external_message_id=sent['id'], external_thread_id=sent.get('threadId') or None)
    store.set_contact(listing_id, 'contacted', channel=channel, subject=subject)
    return {'sent': True, 'message_id': sent['id'], 'thread_id': sent.get('threadId') or None}


def command_submitted(store, value):
    listing_id, text, url = value.get('listing'), value.get('text'), value.get('url')
    if not isinstance(text, str) or not text.strip() or not isinstance(url, str):
        raise ValueError('submitted needs listing, the text you submitted and the form URL.')
    contact_of(store, listing_id)
    identifier = f'form:{uuid4()}'
    store.correspond(listing_id, 'outgoing', 'contact_form', text, external_message_id=identifier, external_thread_id=url)
    store.set_contact(listing_id, 'contacted', channel='contact_form')
    return {'recorded': True, 'message_id': identifier}


def command_ask(store, value):
    listing_id, question = value.get('listing'), value.get('question')
    if not isinstance(question, str) or not question.strip():
        raise ValueError('ask needs listing and question.')
    listing = contact_of(store, listing_id)
    link = listing['sources'][0]['url']
    try:
        result = tell_user(f'Question about {label(listing)} ({link}):\n\n{question.strip()}\n\n'
                           'Reply here, and I will continue with the listing agent.')
    except NotConnected as error:
        store.set_contact(listing_id, 'failed', error=f'A question for you could not be sent. {error} The question: {question.strip()}')
        raise
    message_id = result.get('message_id')
    return {'question': store.ask_user(listing_id, question, field=value.get('field'), purpose=value.get('purpose'),
                                       external_message_id=str(message_id) if message_id is not None else None)}


def command_tell(store, value):
    text = value.get('text')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('tell needs text.')
    if value.get('listing'):
        listing = contact_of(store, value['listing'])
        text = f'{label(listing)} ({listing["sources"][0]["url"]}):\n\n{text.strip()}'
    tell_user(text)
    return {'told': True}


def command_answer(store, value):
    if type(value.get('question')) is not int or not isinstance(value.get('answer'), str):
        raise ValueError('answer needs the question number and the answer.')
    store.answer_question(value['question'], value['answer'], share=value.get('share'))
    return {'answered': value['question']}


def within_availability(availability, start):
    local = start.astimezone(ZoneInfo(availability['timezone']))
    end = local + timedelta(minutes=VIEWING_MINUTES)
    if end.date() != local.date():
        return False
    for period in availability.get('unavailable', []):
        if date.fromisoformat(period['from']) <= local.date() <= date.fromisoformat(period['to']):
            return False
    day = WEEKDAYS[local.weekday()]
    return any(window['day'] == day and window['start'] <= local.strftime('%H:%M') and end.strftime('%H:%M') <= window['end']
               for window in availability.get('weekly', []))


def calendar_conflicts(store, start):
    """All-day events are left out: the saved availability's unavailable dates are where whole days off live. Events
    of agreed and booked viewings are left out too: the store's overlap rule decides about those, with its own answer,
    and a moved viewing must not clash with its own event."""
    end = start + timedelta(minutes=VIEWING_MINUTES)
    viewings = {event_id for viewing in store.scheduled_viewings()
                for event_id in (viewing['calendar_event_id'], created_event_id(viewing['id']))}
    events = google_api('calendar', 'list', '--start', start.isoformat(), '--end', end.isoformat(), '--max', '10')
    return [event for event in events
            if 'T' in event.get('start', '') and event.get('status') != 'cancelled' and event.get('id') not in viewings]


def created_event_id(viewing_id):
    """The identifier of the event this script creates for a viewing. Calendar event identifiers allow only lowercase
    a to v and digits, 5 to 1024 characters (Calendar API, Events resource `id`), which a UUID without hyphens satisfies."""
    return viewing_id.replace('-', '')


def local_start(store, viewing):
    zone = (store.settings().get('viewing_availability') or {}).get('timezone', 'UTC')
    return datetime.fromisoformat(viewing['time']).astimezone(ZoneInfo(zone)), zone


def viewing_event(store, viewing):
    listing = store.listing(viewing['listing_id'])
    agent = listing['contact']['agent'] if listing['contact'] else listing['agent']
    start, zone = local_start(store, viewing)
    lines = [label(listing), *(f'{source["label"]}: {source["url"]}' for source in listing['sources']), '',
             *(f'{name}: {agent[key]}' for key, name in AGENT_DETAILS if agent.get(key))]
    if viewing['details'].strip():
        lines += ['', viewing['details'].strip()]
    return {'summary': f'Apartment viewing: {listing.get("address") or label(listing)}', 'location': listing.get('address') or '',
            'description': '\n'.join(lines), 'status': 'confirmed',
            'start': {'dateTime': start.isoformat(), 'timeZone': zone},
            'end': {'dateTime': (start + timedelta(minutes=VIEWING_MINUTES)).isoformat(), 'timeZone': zone}}


def write_calendar(*arguments):
    script = Path(__file__).resolve().with_name('viewing_calendar.py')
    return json.loads(run([sys.executable, str(script), *arguments], 'Google Calendar ' + arguments[0]))


def save_event(store, viewing):
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.json', delete=False) as file:
        json.dump({'event_id': viewing['calendar_event_id'], 'new_id': created_event_id(viewing['id']),
                   'event': viewing_event(store, viewing)}, file, ensure_ascii=False)
    try:
        saved = write_calendar('save', file.name)
    finally:
        os.unlink(file.name)
    return store.record_booking(viewing['id'], viewing['time'], viewing['details'], saved['id'])


def remove_event(store, viewing):
    write_calendar('delete', viewing['calendar_event_id'])
    store.record_event_removed(viewing['id'], viewing['calendar_event_id'])
    return False


def sync_calendar(store):
    """Returns the ids of the viewings it booked and, per viewing whose write failed, the viewing and the error. A
    failed write does not hold up the others. A calendar that is not connected fails every remaining write unsent."""
    booked, failures, disconnected = set(), {}, None
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
            failures[viewing['id']] = (viewing, str(error.args[0] if isinstance(error, KeyError) else error))
    return booked, failures


def sync_after(store, viewing_id):
    """Runs after a command has committed its change, so a failure is reported in its output and never fails the
    command, which would otherwise read as "nothing was recorded"."""
    try:
        booked, failures = sync_calendar(store)
    except sqlite3.Error as error:
        return False, f'The calendar was not updated, because the store could not be read: {error}'
    return viewing_id in booked, failures[viewing_id][1] if viewing_id in failures else None


def calendar_notices(store, failures):
    """One notice per distinct error, naming the viewings it holds up."""
    viewings_by_error = {}
    for viewing, error in failures.values():
        viewings_by_error.setdefault(error, []).append(viewing)
    notices = {}
    for error, viewings in viewings_by_error.items():
        names = ', '.join(f'{label(store.listing(viewing["listing_id"]))} on {local_start(store, viewing)[0]:%a %d %b at %H:%M}'
                          for viewing in viewings)
        notices['calendar: ' + error] = (f'I could not update your Google Calendar for the viewing of {names}: {error} '
                                         'I will keep trying every few minutes.')
    return notices


def command_agree(store, value):
    listing_id, time, details = value.get('listing'), value.get('time'), value.get('details', '')
    contact_of(store, listing_id)
    start = datetime.fromisoformat(viewing_time(time))
    if value.get('user_confirmed') is not True:
        availability = store.settings().get('viewing_availability')
        if not availability or not within_availability(availability, start):
            raise WaitingForUser(f'{time} is outside the user\'s viewing availability. Ask the user before agreeing to it.')
        require('calendar')
        conflicts = calendar_conflicts(store, start)
        if conflicts:
            names = ', '.join(f'{event.get("summary", "an event")} ({event["start"]} to {event["end"]})' for event in conflicts)
            raise WaitingForUser(f'The calendar has {names} at {time}. Ask the user before agreeing to it.')
    try:
        viewing_id = store.agree_viewing(listing_id, time, details, viewing_id=value.get('viewing'))
    except ViewingConflict as conflict:
        raise WaitingForUser(f'{conflict} Propose another time.') from None
    booked, error = sync_after(store, viewing_id)
    return {'viewing': viewing_id, 'booked': booked, 'calendar_error': error}


def command_work(store):
    due = []
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


def record(store, listing_id, channel, message_id, thread_id, text):
    if not store.has_message(listing_id, channel, message_id):
        store.correspond(listing_id, 'incoming', channel, (text or '').strip()[:20000] or '(The message has no text.)',
                         external_message_id=message_id, external_thread_id=thread_id)


def only(listing_ids):
    """A reply outside a known thread is filed only when exactly one open contact uses that address or number."""
    return listing_ids[0] if len(listing_ids) == 1 else None


def poll_gmail(store, contacts):
    owners = store.thread_owners('email')
    senders = {}
    queries = set()
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
    seen = set()
    for query in sorted(queries):
        for message in google_api('gmail', 'search', query, '--max', '25'):
            if message['id'] in seen:
                continue
            seen.add(message['id'])
            listing_id = owners.get(message.get('threadId')) or only(senders.get(parseaddr(message.get('from', ''))[1].lower(), []))
            if listing_id and not store.has_message(listing_id, 'email', message['id']):
                full = google_api('gmail', 'get', message['id'])
                record(store, listing_id, 'email', message['id'], message.get('threadId'), full.get('body') or full.get('snippet'))


def poll_imessage(store, contacts):
    owners = store.thread_owners('imessage')
    requested = {contact['listing_id']: contact['requested_at'] for contact in contacts}
    phones = [(contact['listing_id'], contact['agent']['phone']) for contact in contacts if contact['agent'].get('phone')]
    if not phones and not owners:
        return
    for chat in imsg('chats', '--limit', '200', '--json'):
        handles = [chat.get('identifier'), *chat.get('participants', [])]
        listing_id = owners.get(chat.get('guid')) or only([listing for listing, phone in phones
                                                            if any(same_handle(phone, handle) for handle in handles)])
        if listing_id not in requested:
            continue
        for message in imsg('history', '--chat-id', str(chat['id']), '--start', requested[listing_id], '--limit', '50', '--json'):
            if not message.get('is_from_me') and message.get('text'):
                record(store, listing_id, 'imessage', message['guid'], message.get('chat_guid') or chat.get('guid'), message['text'])


def notice_state_path():
    return Path(os.environ['HERMES_HOME']) / 'apt-scraper' / 'contact-notices.json'


def report_errors(notices):
    """Tells the user once about each new error of a background step, so an outage does not message them every tick.
    `notices` maps each failing step to what the user is told."""
    path = notice_state_path()
    try:
        reported = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        reported = {}
    for step, notice in notices.items():
        if reported.get(step) != notice:
            try:
                tell_user(notice)
            except (NotConnected, RuntimeError, OSError) as failure:
                print(f'Telling the user "{notice}" did not work: {failure}', file=sys.stderr)
                continue
            reported[step] = notice
    current = {step: notice for step, notice in reported.items() if step in notices}
    if current:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current), encoding='utf-8')
    else:
        path.unlink(missing_ok=True)


def command_due(store):
    contacts = store.active_contacts()
    notices = {}
    for channel, poll in (('gmail', poll_gmail), ('imessage', poll_imessage)):
        if not contacts:
            continue
        try:
            if usable(channel):
                poll(store, contacts)
        except (RuntimeError, ValueError, KeyError, OSError) as error:
            notices[channel] = (f'I could not check {CHANNEL_LABELS[channel]} for replies from listing agents: {error} '
                                'I will keep trying every few minutes.')
    notices.update(calendar_notices(store, sync_calendar(store)[1]))
    report_errors(notices)
    due = store.due_contacts()
    starts = sum(item['reason'] == 'start' for item in due)
    replies = len(due) - starts
    if starts:
        print(f'{starts} new contact request{"" if starts == 1 else "s"}')
    if replies:
        print(f'{replies} repl{"y" if replies == 1 else "ies"} to answer')
    print(json.dumps({'wakeAgent': bool(due)}))
    return 0


FILE_COMMANDS = {'send': command_send, 'submitted': command_submitted, 'ask': command_ask, 'tell': command_tell,
                 'answer': command_answer, 'agree': command_agree}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Correspond with listing agents and ask the user about listings.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('work', 'questions', 'due'):
        commands.add_parser(name)
    for name in FILE_COMMANDS:
        commands.add_parser(name).add_argument('file', type=Path)
    for name in ('done', 'close'):
        commands.add_parser(name).add_argument('listing')
    commands.add_parser('cancel').add_argument('viewing')
    failing = commands.add_parser('fail')
    failing.add_argument('listing')
    failing.add_argument('error')
    arguments = parser.parse_args(argv)
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
        result = {'canceled': arguments.viewing, 'calendar_error': sync_after(store, arguments.viewing)[1]}
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
        print(error.args[0] if isinstance(error, KeyError) else error, file=sys.stderr)
        sys.exit(1)
