import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from schema import content_fingerprint, migrate
from queries import browse, snapshot, validate_page


UNCHANGED = object()
CONTACT_CHANNELS = {'email', 'imessage', 'contact_form'}


def require_text(value, name, *, limit=20000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} must contain 1 to {limit} characters.')
    return value.strip()


def require_url(value):
    require_text(value, 'URL')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('URLs must use http or https without credentials.')


def validate_listing(listing):
    fields = {'id', 'title', 'price', 'currency', 'size', 'rooms', 'address', 'description',
              'amenities', 'photos', 'sources', 'first_seen', 'status', 'rental_period', 'agent', 'price_note'}
    if not isinstance(listing, dict) or not {'id', 'sources'} <= set(listing) or not set(listing) <= fields:
        raise ValueError('A listing needs id and sources, with only documented listing fields.')
    listing = {**dict.fromkeys(fields), 'amenities': [], 'photos': [], 'agent': {},
               'first_seen': datetime.now(timezone.utc).date().isoformat(), 'status': 'available', **listing}
    for field in fields - {'price', 'agent', 'amenities', 'photos', 'sources'}:
        if listing[field] is not None:
            if not isinstance(listing[field], str):
                raise ValueError(f'{field} must be text or null.')
            listing[field] = listing[field].strip() or None
    require_text(listing['id'], 'id')
    if not re.fullmatch(r'[A-Za-z0-9:._-]{1,200}', listing['id']):
        raise ValueError('Listing id must use letters, digits, colons, dots, underscores or hyphens.')
    if isinstance(listing['price'], str):
        listing['price_note'] = require_text(listing['price'], 'Price description', limit=1000)
        listing['price'] = None
    if listing['price'] is not None and (type(listing['price']) not in (int, float) or not math.isfinite(listing['price']) or listing['price'] < 0):
        raise ValueError('Price must be a finite nonnegative number.')
    if listing['rental_period'] not in (None, 'day', 'week', 'month', 'year', 'total'):
        raise ValueError('Rental period must be day, week, month, year or total.')
    validate_agent(listing['agent'])
    if listing['currency'] is not None and not re.fullmatch(r'[A-Z]{3}', listing['currency']):
        raise ValueError('Currency must be a three-letter currency code.')
    if listing['status'] not in ('available', 'unavailable'):
        raise ValueError('Status must be available or unavailable.')
    if date.fromisoformat(listing['first_seen']).isoformat() != listing['first_seen']:
        raise ValueError('First seen must use YYYY-MM-DD.')
    for field in ('amenities', 'photos', 'sources'):
        if not isinstance(listing[field], list):
            raise ValueError(f'{field} must be an array.')
    for amenity in listing['amenities']:
        require_text(amenity, 'Amenity')
    for photo in listing['photos']:
        if not isinstance(photo, str) or not re.fullmatch(r'/media/[A-Za-z0-9._-]+', photo):
            require_url(photo)
    if not listing['sources']:
        raise ValueError('At least one source is required.')
    for source in listing['sources']:
        if not isinstance(source, dict) or set(source) != {'label', 'url'}:
            raise ValueError('A source needs a label and URL.')
        require_text(source['label'], 'Source label')
        require_url(source['url'])
    return listing


def validate_agent(agent):
    if not isinstance(agent, dict) or not set(agent) <= {'name', 'email', 'phone', 'contact_form'}:
        raise ValueError('Agent accepts name, email, phone and contact_form.')
    for name, value in agent.items():
        require_text(value, name, limit=1000)
        if name == 'contact_form':
            require_url(value)


class ListingStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            migrate(connection)

    @property
    def schema_version(self):
        with self.connect() as connection:
            return connection.execute('PRAGMA user_version').fetchone()[0]

    @contextmanager
    def connect(self, write=False):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        try:
            with connection:
                if write:
                    connection.execute('BEGIN IMMEDIATE')
                before = connection.total_changes
                yield connection
                if write and connection.total_changes != before:
                    connection.execute('UPDATE changes SET revision = revision + 1 WHERE id = 1')
        finally:
            connection.close()

    def upsert_listing(self, listing):
        listing = validate_listing(listing)
        with self.connect(write=True) as connection:
            previous = connection.execute('SELECT * FROM listings WHERE id = ?', (listing['id'],)).fetchone()
            if previous:
                listing['first_seen'] = previous['first_seen']
            fingerprint = content_fingerprint(listing)
            changed = not previous or previous['fingerprint'] != fingerprint
            analysis = connection.execute('SELECT * FROM ranking WHERE listing_id = ?', (listing['id'],)).fetchone()
            needs_analysis = not analysis or analysis['fingerprint'] != fingerprint or analysis['features'] is None
            if previous and not changed and previous['status'] == listing['status']:
                return {'changed': False, 'needs_analysis': needs_analysis, 'content_fingerprint': fingerprint}
            connection.execute(
                '''INSERT INTO listings (id, data, status, first_seen, fingerprint) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET data = excluded.data, status = excluded.status, fingerprint = excluded.fingerprint''',
                (listing['id'], json.dumps(listing, ensure_ascii=False), listing['status'], listing['first_seen'], fingerprint),
            )
            return {'changed': bool(changed), 'needs_analysis': needs_analysis, 'content_fingerprint': fingerprint}

    def settings(self):
        with self.connect() as connection:
            return {row['name']: json.loads(row['value']) for row in connection.execute('SELECT * FROM settings')}

    def has_listings(self):
        with self.connect() as connection:
            return connection.execute('SELECT 1 FROM listings LIMIT 1').fetchone() is not None

    def set_availability(self, listing_id, status):
        if status not in ('available', 'unavailable'):
            raise ValueError('Status must be available or unavailable.')
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            connection.execute("UPDATE listings SET status = ?, data = json_set(data, '$.status', ?) WHERE id = ? AND status != ?",
                               (status, status, listing_id, status))

    def set_matches_criteria(self, listing_id, matches):
        if type(matches) is not bool:
            raise ValueError('Criteria eligibility must be true or false.')
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            connection.execute('UPDATE listings SET matches_criteria = ? WHERE id = ? AND matches_criteria != ?', (matches, listing_id, matches))

    def update_settings(self, **settings):
        if not settings or not set(settings) <= {'criteria', 'preferences', 'listing_sites', 'viewing_availability'}:
            raise ValueError('Unknown or empty settings.')
        for name, value in settings.items():
            if name == 'listing_sites':
                if not isinstance(value, list):
                    raise ValueError('Listing sites must be an array of URLs.')
                for url in value:
                    require_url(url)
            elif not isinstance(value, dict):
                raise ValueError(f'{name} must be an object.')
            json.dumps(value, allow_nan=False)
        with self.connect(write=True) as connection:
            connection.executemany('INSERT INTO settings VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value',
                                   [(name, json.dumps(value, allow_nan=False)) for name, value in settings.items()])

    def analyze(self, listing_id, fingerprint, features):
        if not isinstance(features, dict):
            raise ValueError('Features must be an object.')
        features = json.dumps(features, allow_nan=False)
        with self.connect(write=True) as connection:
            self.require_fingerprint(connection, listing_id, fingerprint)
            connection.execute('''INSERT INTO ranking (listing_id, fingerprint, features) VALUES (?, ?, ?)
                ON CONFLICT(listing_id) DO UPDATE SET fingerprint = excluded.fingerprint, features = excluded.features,
                rank = CASE WHEN ranking.fingerprint = excluded.fingerprint THEN ranking.rank END,
                reason = CASE WHEN ranking.fingerprint = excluded.fingerprint THEN ranking.reason END''', (listing_id, fingerprint, features))

    def features(self, listing_id):
        with self.connect() as connection:
            self.require_listing(connection, listing_id)
            row = connection.execute('SELECT features FROM ranking JOIN listings ON listings.id = ranking.listing_id WHERE listing_id = ? AND ranking.fingerprint = listings.fingerprint', (listing_id,)).fetchone()
            return json.loads(row['features']) if row and row['features'] else None

    def rank(self, listing_id, fingerprint, rank, reason):
        if type(rank) is not int or rank < 1:
            raise ValueError('Rank must be a positive integer.')
        reason = require_text(reason, 'Reason', limit=500)
        with self.connect(write=True) as connection:
            self.require_fingerprint(connection, listing_id, fingerprint)
            connection.execute('''INSERT INTO ranking (listing_id, fingerprint, rank, reason) VALUES (?, ?, ?, ?)
                ON CONFLICT(listing_id) DO UPDATE SET rank = excluded.rank, reason = excluded.reason,
                features = CASE WHEN ranking.fingerprint = excluded.fingerprint THEN ranking.features END,
                fingerprint = excluded.fingerprint''', (listing_id, fingerprint, rank, reason))

    @staticmethod
    def require_fingerprint(connection, listing_id, fingerprint):
        ListingStore.require_listing(connection, listing_id)
        if connection.execute('SELECT fingerprint FROM listings WHERE id = ?', (listing_id,)).fetchone()[0] != fingerprint:
            raise ValueError('Listing content changed. Read the current listing before saving analysis or rank.')

    def listing(self, listing_id, *, notes_page=1, correspondence_page=1, viewings_page=1, visible_only=False):
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
            if not row or (visible_only and not row['matches_criteria']):
                raise KeyError('Listing not found.')
            listing = json.loads(row['data'])
            listing['matches_criteria'] = bool(row['matches_criteria'])
            ranking = connection.execute('SELECT * FROM ranking WHERE listing_id = ?', (listing_id,)).fetchone()
            listing.update(rank=ranking['rank'] if ranking else None, reason=ranking['reason'] if ranking else None,
                           content_fingerprint=row['fingerprint'], analysis_current=bool(ranking and ranking['fingerprint'] == row['fingerprint']))
            feedback = connection.execute('SELECT * FROM feedback WHERE listing_id = ?', (listing_id,)).fetchone()
            listing.update(favorite=bool(feedback and feedback['favorite']), dismissed=bool(feedback and feedback['dismissed']))
            contact = connection.execute('SELECT * FROM contacts WHERE listing_id = ?', (listing_id,)).fetchone()
            listing['contact'] = dict(contact) if contact else None
            if contact:
                listing['contact']['agent'] = json.loads(contact['agent'])
            for key, page in [('notes', notes_page), ('correspondence', correspondence_page), ('viewings', viewings_page)]:
                validate_page(page, 50)
                listing[key + '_total'] = connection.execute(f'SELECT COUNT(*) FROM {key} WHERE listing_id = ?', (listing_id,)).fetchone()[0]
                order = 'time, id' if key == 'viewings' else 'id DESC'
                rows = [dict(item) for item in connection.execute(
                    f'SELECT * FROM {key} WHERE listing_id = ? ORDER BY {order} LIMIT 50 OFFSET ?', (listing_id, (page - 1) * 50))]
                listing[key] = rows if key == 'viewings' else list(reversed(rows))
            return listing

    def state(self):
        with self.connect() as connection:
            return snapshot(connection)

    def revision(self):
        with self.connect() as connection:
            return connection.execute('SELECT revision FROM changes WHERE id = 1').fetchone()[0]

    def start_chat(self, text, request_id):
        text = require_text(text, 'Message')
        request_id = require_text(request_id, 'Request id', limit=100)
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute("SELECT 1 FROM chat_messages WHERE status = 'running'").fetchone():
                raise ValueError('A reply is already in progress.')
            if connection.execute('SELECT 1 FROM chat_messages WHERE request_id = ?', (request_id,)).fetchone():
                raise ValueError('This message was already submitted. Refresh the conversation.')
            now = datetime.now(timezone.utc).isoformat()
            connection.execute("INSERT INTO chat_messages (role, content, status, created_at, request_id) VALUES ('user', ?, 'complete', ?, ?)", (text, now, request_id))
            return connection.execute("INSERT INTO chat_messages (role, content, status, created_at) VALUES ('agent', '', 'running', ?)", (now,)).lastrowid

    def update_chat(self, message_id, *, content=None, status=None, events=None, prompt=UNCHANGED, error=UNCHANGED, session_id=None, admission_id=None):
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if status is not None and status not in ('running', 'complete', 'error'):
                raise ValueError('Unknown message status.')
            connection.execute('''UPDATE chat_messages SET content = COALESCE(?, content), status = COALESCE(?, status),
                events = COALESCE(?, events), prompt = CASE WHEN ? THEN ? ELSE prompt END,
                error = CASE WHEN ? THEN ? ELSE error END, admission_id = COALESCE(?, admission_id) WHERE id = ?''',
                (content, status, json.dumps(events) if events is not None else None,
                 prompt is not UNCHANGED, json.dumps(prompt) if prompt is not UNCHANGED and prompt is not None else None,
                 error is not UNCHANGED, error if error is not UNCHANGED else None, admission_id, message_id))
            if session_id:
                connection.execute('UPDATE chat_session SET session_id = ? WHERE id = 1', (session_id,))

    def chat_state(self, page=1):
        validate_page(page, 20)
        with self.connect() as connection:
            rows = connection.execute('SELECT * FROM chat_messages ORDER BY id DESC LIMIT 20 OFFSET ?', ((page - 1) * 20,))
            messages = [{**dict(row), 'events': json.loads(row['events']), 'prompt': json.loads(row['prompt']) if row['prompt'] else None} for row in rows]
            return {'messages': list(reversed(messages)), 'total': connection.execute('SELECT COUNT(*) FROM chat_messages').fetchone()[0],
                    'active': bool(connection.execute("SELECT 1 FROM chat_messages WHERE status = 'running'").fetchone()),
                    'session_id': connection.execute('SELECT session_id FROM chat_session WHERE id = 1').fetchone()[0]}

    def browse(self, view='Rankings', *, page=1, page_size=24):
        with self.connect() as connection:
            return browse(connection, view, page, page_size)

    def feedback(self, listing_id, *, favorite=None, dismissed=None):
        for value in (favorite, dismissed):
            if value is not None and type(value) is not bool:
                raise ValueError('Feedback must be true or false.')
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            connection.execute('INSERT OR IGNORE INTO feedback (listing_id) VALUES (?)', (listing_id,))
            if favorite is not None:
                connection.execute('UPDATE feedback SET favorite = ? WHERE listing_id = ?', (favorite, listing_id))
            if dismissed is not None:
                connection.execute('UPDATE feedback SET dismissed = ? WHERE listing_id = ?', (dismissed, listing_id))
            connection.execute('UPDATE feedback SET favorite = 0 WHERE listing_id = ? AND dismissed = 1', (listing_id,))

    def add_note(self, text, *, listing_id, role='user'):
        text = require_text(text, 'Note')
        if role not in ('user', 'agent'):
            raise ValueError('Note role must be user or agent.')
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            connection.execute('INSERT INTO notes (listing_id, role, text, created_at) VALUES (?, ?, ?, ?)',
                               (listing_id, role, text, datetime.now(timezone.utc).isoformat()))

    def request_contact(self, listing_id):
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            agent = connection.execute("SELECT json_extract(data, '$.agent') FROM listings WHERE id = ?", (listing_id,)).fetchone()[0]
            connection.execute('INSERT OR IGNORE INTO contacts (listing_id, status, requested_at, agent) VALUES (?, ?, ?, ?)',
                               (listing_id, 'pending', datetime.now(timezone.utc).isoformat(), agent))
            connection.execute("UPDATE contacts SET status = 'pending', error = NULL WHERE listing_id = ? AND status = 'failed'", (listing_id,))

    def correspond(self, listing_id, direction, channel, text, *, external_message_id, external_thread_id=None):
        text = require_text(text, 'Message')
        if channel not in CONTACT_CHANNELS:
            raise ValueError('Unknown contact channel.')
        require_text(external_message_id, 'External message id', limit=1000)
        if external_thread_id is not None:
            require_text(external_thread_id, 'External thread id', limit=1000)
        if direction not in ('incoming', 'outgoing'):
            raise ValueError('Direction must be incoming or outgoing.')
        with self.connect(write=True) as connection:
            if not connection.execute('SELECT 1 FROM contacts WHERE listing_id = ?', (listing_id,)).fetchone():
                raise KeyError('Contact request not found.')
            now = datetime.now(timezone.utc).isoformat()
            return bool(connection.execute('''INSERT INTO correspondence
                (listing_id, direction, channel, text, created_at, external_message_id, external_thread_id) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(listing_id, channel, external_message_id) DO NOTHING''',
                (listing_id, direction, channel, text, now, external_message_id, external_thread_id)).rowcount)

    def set_contact(self, listing_id, status, *, channel=None, agent=None, error=None):
        if status not in ('pending', 'in_progress', 'contacted', 'failed', 'closed'):
            raise ValueError('Unknown contact status.')
        if channel is not None and channel not in CONTACT_CHANNELS:
            raise ValueError('Unknown contact channel.')
        if status == 'failed':
            error = require_text(error, 'Contact error')
        elif error is not None:
            raise ValueError('Only a failed contact can have an error.')
        if agent is not None:
            validate_agent(agent)
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            current = connection.execute('SELECT agent FROM contacts WHERE listing_id = ?', (listing_id,)).fetchone()
            if not current:
                raise KeyError('Contact request not found.')
            connection.execute('UPDATE contacts SET status = ?, channel = COALESCE(?, channel), agent = ?, error = ? WHERE listing_id = ?',
                               (status, channel, json.dumps(agent) if agent is not None else current['agent'], error, listing_id))

    def approve_information(self, listing_id, field, value, purpose):
        field = require_text(field, 'Field', limit=200)
        purpose = require_text(purpose, 'Purpose', limit=1000)
        value = require_text(value, 'Approved value')
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            return connection.execute('INSERT INTO approvals (listing_id, field, value, purpose, approved_at) VALUES (?, ?, ?, ?, ?)',
                                      (listing_id, field, value, purpose, datetime.now(timezone.utc).isoformat())).lastrowid

    def approved_information(self, listing_id, purpose):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM approvals WHERE listing_id = ? AND purpose = ? AND revoked_at IS NULL ORDER BY id',
                                                            (listing_id, purpose))]

    def revoke_approval(self, approval_id):
        with self.connect(write=True) as connection:
            if not connection.execute('UPDATE approvals SET revoked_at = ? WHERE id = ?',
                                      (datetime.now(timezone.utc).isoformat(), approval_id)).rowcount:
                raise KeyError('Approval not found.')

    def book_viewing(self, listing_id, time, details, calendar_event_id, *, viewing_id=None):
        time = datetime.fromisoformat(time)
        if time.utcoffset() is None:
            raise ValueError('Viewing time must include its UTC offset.')
        time = time.astimezone(timezone.utc).isoformat()
        require_text(calendar_event_id, 'Calendar event id', limit=1000)
        if not isinstance(details, str):
            raise ValueError('Viewing details must be text.')
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            if viewing_id:
                if not connection.execute('SELECT 1 FROM viewings WHERE id = ? AND listing_id = ?', (viewing_id, listing_id)).fetchone():
                    raise KeyError('Viewing not found for this listing.')
                connection.execute("UPDATE viewings SET time = ?, details = ?, calendar_event_id = ?, status = 'booked' WHERE id = ?",
                                   (time, details, calendar_event_id, viewing_id))
            else:
                viewing_id = str(uuid4())
                connection.execute('INSERT INTO viewings (id, listing_id, time, details, calendar_event_id) VALUES (?, ?, ?, ?, ?)',
                                   (viewing_id, listing_id, time, details, calendar_event_id))
            return viewing_id

    def cancel_viewing(self, viewing_id):
        with self.connect(write=True) as connection:
            if not connection.execute("UPDATE viewings SET status = 'canceled' WHERE id = ?", (viewing_id,)).rowcount:
                raise KeyError('Viewing not found.')

    def remove_viewing(self, viewing_id):
        with self.connect(write=True) as connection:
            if not connection.execute('DELETE FROM viewings WHERE id = ?', (viewing_id,)).rowcount:
                raise KeyError('Viewing not found.')

    @staticmethod
    def require_listing(connection, listing_id):
        if not connection.execute('SELECT 1 FROM listings WHERE id = ?', (listing_id,)).fetchone():
            raise KeyError('Listing not found.')
