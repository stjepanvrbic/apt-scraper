import hashlib
import json


def content_fingerprint(listing):
    content = {key: value for key, value in listing.items() if key not in ('id', 'first_seen', 'status')}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def initial_schema(connection):
    statements = [
        "CREATE TABLE listings (id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL, first_seen TEXT NOT NULL, fingerprint TEXT NOT NULL, matches_criteria INTEGER NOT NULL DEFAULT 1)",
        'CREATE TABLE ranking (listing_id TEXT PRIMARY KEY REFERENCES listings(id), fingerprint TEXT NOT NULL, features TEXT, rank INTEGER, reason TEXT)',
        'CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)',
        'CREATE TABLE feedback (listing_id TEXT PRIMARY KEY REFERENCES listings(id), favorite INTEGER NOT NULL DEFAULT 0, dismissed INTEGER NOT NULL DEFAULT 0)',
        'CREATE TABLE notes (id INTEGER PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES listings(id), role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL)',
        "CREATE TABLE contacts (listing_id TEXT PRIMARY KEY REFERENCES listings(id), status TEXT NOT NULL, requested_at TEXT NOT NULL, channel TEXT, agent TEXT NOT NULL DEFAULT '{}', error TEXT)",
        'CREATE TABLE correspondence (id INTEGER PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES listings(id), direction TEXT NOT NULL, channel TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL, external_message_id TEXT NOT NULL, external_thread_id TEXT, UNIQUE(listing_id, channel, external_message_id))',
        'CREATE TABLE approvals (id INTEGER PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES listings(id), field TEXT NOT NULL, value TEXT NOT NULL, purpose TEXT NOT NULL, approved_at TEXT NOT NULL, revoked_at TEXT)',
        "CREATE TABLE viewings (id TEXT PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES listings(id), time TEXT NOT NULL, details TEXT NOT NULL, calendar_event_id TEXT UNIQUE, status TEXT NOT NULL DEFAULT 'booked')",
        'CREATE INDEX listings_status ON listings(matches_criteria, status)',
        'CREATE INDEX ranking_order ON ranking(rank, listing_id)',
        'CREATE INDEX notes_listing ON notes(listing_id, id)',
        'CREATE INDEX correspondence_listing ON correspondence(listing_id, id)',
        'CREATE INDEX viewings_schedule ON viewings(status, time, id)',
        'CREATE INDEX viewings_listing ON viewings(listing_id)',
        'CREATE TABLE changes (id INTEGER PRIMARY KEY CHECK (id = 1), revision INTEGER NOT NULL)',
        'INSERT INTO changes VALUES (1, 1)',
        "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL, events TEXT NOT NULL DEFAULT '[]', prompt TEXT, error TEXT, created_at TEXT NOT NULL, request_id TEXT UNIQUE, admission_id TEXT)",
        'CREATE TABLE chat_session (id INTEGER PRIMARY KEY CHECK (id = 1), session_id TEXT)',
        'INSERT INTO chat_session VALUES (1, NULL)',
    ]
    for statement in statements:
        connection.execute(statement)


def contact_workflow(connection):
    statements = [
        'ALTER TABLE contacts ADD COLUMN handled_through INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE contacts ADD COLUMN subject TEXT',
        'CREATE TABLE questions (id INTEGER PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES listings(id), question TEXT NOT NULL, field TEXT, purpose TEXT, asked_at TEXT NOT NULL, external_message_id TEXT, answer TEXT, answered_at TEXT, handled INTEGER NOT NULL DEFAULT 0)',
        'CREATE INDEX questions_open ON questions(listing_id) WHERE answered_at IS NULL',
    ]
    for statement in statements:
        connection.execute(statement)


def ranking_readings(connection):
    connection.execute('CREATE TABLE note_readings (note_id INTEGER PRIMARY KEY REFERENCES notes(id), reading TEXT NOT NULL)')
    connection.execute('CREATE TABLE preference_readings (phrase TEXT PRIMARY KEY, reading TEXT NOT NULL)')


MIGRATIONS = (initial_schema, contact_workflow, ranking_readings)


def migrate(connection):
    connection.execute('BEGIN IMMEDIATE')
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version > len(MIGRATIONS):
        raise ValueError('This database uses a newer schema. Update the gallery before opening it.')
    for index in range(version, len(MIGRATIONS)):
        MIGRATIONS[index](connection)
        connection.execute(f'PRAGMA user_version = {index + 1}')
