import hashlib
import json

from code_features import store_all_code_features


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


def code_derived_features(connection):
    connection.execute("""CREATE TABLE code_features (listing_id TEXT PRIMARY KEY REFERENCES listings(id), fingerprint TEXT NOT NULL,
        monthly_price REAL, currency TEXT, size_sqft REAL, price_per_sqft REAL, bedrooms INTEGER, bathrooms REAL, unit_label TEXT,
        unit_floor INTEGER, unit_level TEXT, photo_count INTEGER NOT NULL, latitude REAL, longitude REAL)""")


def listing_features(connection):
    statements = [
        'CREATE TABLE feature_records (listing_id TEXT PRIMARY KEY REFERENCES listings(id), fingerprint TEXT NOT NULL, extraction TEXT NOT NULL, extracted_at TEXT NOT NULL, preferences TEXT NOT NULL)',
        'CREATE TABLE tags (tag TEXT PRIMARY KEY, added_at TEXT NOT NULL)',
        'CREATE TABLE model_reads (read TEXT PRIMARY KEY, attempts INTEGER NOT NULL)',
    ]
    for statement in statements:
        connection.execute(statement)


def scoring_model(connection):
    statements = [
        'ALTER TABLE ranking ADD COLUMN explanation TEXT',
        'ALTER TABLE ranking ADD COLUMN exploration INTEGER NOT NULL DEFAULT 0',
        'CREATE TABLE reranks (id INTEGER PRIMARY KEY, finished_at TEXT NOT NULL, result TEXT NOT NULL)',
        'CREATE TABLE note_readings (note_id INTEGER PRIMARY KEY REFERENCES notes(id), reading TEXT NOT NULL, read_at TEXT NOT NULL)',
        'CREATE TABLE preference_readings (kind TEXT NOT NULL, phrase TEXT NOT NULL, features TEXT NOT NULL, read_at TEXT NOT NULL, PRIMARY KEY (kind, phrase))',
        'CREATE TABLE pairwise_answers (id INTEGER PRIMARY KEY, chosen TEXT NOT NULL REFERENCES listings(id), other TEXT NOT NULL REFERENCES listings(id), answered_at TEXT NOT NULL)',
    ]
    for statement in statements:
        connection.execute(statement)


def pairwise_skips(connection):
    connection.execute('''CREATE TABLE pairwise_skips (
        first TEXT NOT NULL REFERENCES listings(id), second TEXT NOT NULL REFERENCES listings(id),
        remaining INTEGER NOT NULL CHECK (remaining > 0), PRIMARY KEY (first, second), CHECK (first < second))''')


def distances(connection):
    """Places become a name and an address, and the code features of every listing are computed again, now with its
    distances. Code features are first computed here, so a store from before them gets them with every column."""
    statements = [
        'ALTER TABLE code_features ADD COLUMN nearest_station TEXT',
        'ALTER TABLE code_features ADD COLUMN station_distance_km REAL',
        "ALTER TABLE code_features ADD COLUMN place_distances_km TEXT NOT NULL DEFAULT '{}'",
        'CREATE TABLE geocodes (address TEXT PRIMARY KEY, latitude REAL NOT NULL, longitude REAL NOT NULL, found TEXT NOT NULL, geocoded_at TEXT NOT NULL)',
        'CREATE TABLE transit_feed (id INTEGER PRIMARY KEY CHECK (id = 1), url TEXT NOT NULL, fetched_at TEXT NOT NULL)',
        'CREATE TABLE stations (id TEXT PRIMARY KEY, name TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL)',
    ]
    for statement in statements:
        connection.execute(statement)
    row = connection.execute("SELECT value FROM settings WHERE name = 'places'").fetchone()
    if row:
        places = [{'name': place['name'], 'address': place['address']} for place in json.loads(row[0])]
        connection.execute("UPDATE settings SET value = ? WHERE name = 'places'", (json.dumps(places),))
    store_all_code_features(connection)


def review_pass(connection):
    connection.execute('CREATE TABLE reviews (rerank_id INTEGER PRIMARY KEY REFERENCES reranks(id), state TEXT NOT NULL, '
                       'attempts INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, snapshot TEXT, result TEXT)')


def implicit_signals(connection):
    connection.execute('''CREATE TABLE implicit_signals (
        event_id TEXT PRIMARY KEY, visit_id TEXT NOT NULL, listing_id TEXT NOT NULL REFERENCES listings(id),
        fingerprint TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('open', 'dwell', 'photo', 'close')),
        occurred_at TEXT NOT NULL, duration_ms INTEGER, photo_url TEXT,
        CHECK ((kind = 'dwell' AND duration_ms >= 0 AND photo_url IS NULL)
            OR (kind = 'photo' AND photo_url IS NOT NULL AND duration_ms IS NULL)
            OR (kind IN ('open', 'close') AND duration_ms IS NULL AND photo_url IS NULL)))''')
    connection.execute('CREATE INDEX implicit_signals_listing_visit ON implicit_signals(listing_id, fingerprint, visit_id)')


def listing_embeddings(connection):
    connection.execute('''CREATE TABLE embeddings (listing_id TEXT NOT NULL REFERENCES listings(id), fingerprint TEXT NOT NULL,
        model TEXT NOT NULL, part TEXT NOT NULL, count INTEGER NOT NULL, vector BLOB NOT NULL, PRIMARY KEY (listing_id, model, part))''')


MIGRATIONS = (initial_schema, contact_workflow, code_derived_features, listing_features, scoring_model, pairwise_skips, distances, review_pass, implicit_signals, listing_embeddings)


def migrate(connection):
    connection.execute('BEGIN IMMEDIATE')
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version > len(MIGRATIONS):
        raise ValueError('This database uses a newer schema. Update the gallery before opening it.')
    for index in range(version, len(MIGRATIONS)):
        MIGRATIONS[index](connection)
        connection.execute(f'PRAGMA user_version = {index + 1}')
