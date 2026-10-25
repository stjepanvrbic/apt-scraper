"""The search's own records, kept next to the listing store: what each card showed when it was last
read, area lookups, blocks by site, runs, when new listings were last reported to the user, and a
problem the cron script found before the search could run."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIRST_BLOCK = timedelta(minutes=30)
SEARCH_SETTINGS = ('criteria', 'listing_sites')
LONGEST_BLOCK = timedelta(days=1)
SCHEMA = (
    'CREATE TABLE IF NOT EXISTS cards (listing_id TEXT PRIMARY KEY, signature TEXT NOT NULL, areas TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS regions (site TEXT PRIMARY KEY, regions TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS blocks (site TEXT PRIMARY KEY, count INTEGER NOT NULL, until TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, report TEXT)',
    'CREATE TABLE IF NOT EXISTS reported (id INTEGER PRIMARY KEY CHECK (id = 1), at TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS problem (id INTEGER PRIMARY KEY CHECK (id = 1), text TEXT NOT NULL)',
)


def parse(text):
    return datetime.fromisoformat(text) if text else None


class SearchState:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            for statement in SCHEMA:
                connection.execute(statement)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def card(self, listing_id):
        with self.connect() as connection:
            row = connection.execute('SELECT signature, areas FROM cards WHERE listing_id = ?', (listing_id,)).fetchone()
            return {'signature': row['signature'], 'areas': json.loads(row['areas'])} if row else None

    def saw(self, listing_id, signature, areas, now):
        with self.connect() as connection:
            connection.execute('''INSERT INTO cards VALUES (?, ?, ?, ?, ?) ON CONFLICT(listing_id)
                DO UPDATE SET signature = excluded.signature, areas = excluded.areas, last_seen = excluded.last_seen''',
                               (listing_id, signature, json.dumps(areas), now.isoformat(), now.isoformat()))

    def first_seen(self):
        with self.connect() as connection:
            return {row['listing_id']: parse(row['first_seen']) for row in connection.execute('SELECT * FROM cards')}

    def regions(self, site):
        with self.connect() as connection:
            row = connection.execute('SELECT regions FROM regions WHERE site = ?', (site,)).fetchone()
            return json.loads(row['regions']) if row else {}

    def save_regions(self, site, regions):
        with self.connect() as connection:
            connection.execute('INSERT INTO regions VALUES (?, ?) ON CONFLICT(site) DO UPDATE SET regions = excluded.regions',
                               (site, json.dumps(regions)))

    def blocked_until(self, site):
        with self.connect() as connection:
            row = connection.execute('SELECT until FROM blocks WHERE site = ?', (site,)).fetchone()
            return parse(row['until']) if row else None

    def block(self, site, now):
        with self.connect() as connection:
            row = connection.execute('SELECT count FROM blocks WHERE site = ?', (site,)).fetchone()
            count = (row['count'] if row else 0) + 1
            until = now + min(FIRST_BLOCK * 2 ** (count - 1), LONGEST_BLOCK)
            connection.execute('INSERT INTO blocks VALUES (?, ?, ?) ON CONFLICT(site) DO UPDATE SET count = excluded.count, until = excluded.until',
                               (site, count, until.isoformat()))
            return until

    def unblock(self, site):
        with self.connect() as connection:
            connection.execute('DELETE FROM blocks WHERE site = ?', (site,))

    def start_run(self, now):
        with self.connect() as connection:
            return connection.execute('INSERT INTO runs (started_at) VALUES (?)', (now.isoformat(),)).lastrowid

    def finish_run(self, run_id, now, report):
        with self.connect() as connection:
            connection.execute('UPDATE runs SET finished_at = ?, report = ? WHERE id = ?', (now.isoformat(), json.dumps(report), run_id))

    def running_since(self):
        with self.connect() as connection:
            row = connection.execute('SELECT started_at FROM runs WHERE finished_at IS NULL ORDER BY id DESC LIMIT 1').fetchone()
            return parse(row['started_at']) if row else None

    def last_run(self):
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
            return {'started_at': row['started_at'], 'finished_at': row['finished_at'], **json.loads(row['report'])} if row else None

    def has_searched(self):
        with self.connect() as connection:
            return connection.execute("SELECT 1 FROM runs WHERE json_array_length(report, '$.sites') > 0 LIMIT 1").fetchone() is not None

    def missing_settings(self, store):
        """The settings the search waits for: the whole interview before its first search, afterwards only the settings
        it reads, so questions the interview gains later never stop a running search."""
        return store.missing_settings(SEARCH_SETTINGS) if self.has_searched() else store.missing_settings()

    def reported_at(self):
        with self.connect() as connection:
            row = connection.execute('SELECT at FROM reported WHERE id = 1').fetchone()
            return parse(row['at']) if row else datetime.min.replace(tzinfo=timezone.utc)

    def mark_reported(self, now):
        with self.connect() as connection:
            connection.execute('INSERT INTO reported VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET at = excluded.at', (now.isoformat(),))

    def problem(self):
        """A problem the cron script found before the search could run, such as a missing environment."""
        with self.connect() as connection:
            row = connection.execute('SELECT text FROM problem WHERE id = 1').fetchone()
            return row['text'] if row else None

    def set_problem(self, text):
        with self.connect() as connection:
            if text:
                connection.execute('INSERT INTO problem VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET text = excluded.text', (text,))
            else:
                connection.execute('DELETE FROM problem')
