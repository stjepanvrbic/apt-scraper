"""The search's own records, kept next to the listing store: what each card showed when it was last
read, area lookups, blocks by site, runs, when new listings were last reported to the user, and a
problem the cron script found before the search could run."""

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, TypedDict, cast

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


class CardRecord(TypedDict):
    """What a card showed when it was last read, and the criteria areas whose searches found it."""
    signature: str
    areas: list[str]


class SiteReport(TypedDict):
    site: str
    new: int
    changed: int
    unchanged: int
    gone: int
    deferred: int
    unreadable: int
    errors: list[str]


class RunReport(TypedDict, total=False):
    """What a run records of itself when it finishes."""
    missing: list[str]
    sites: list[SiteReport]
    saved: int
    rejected: list[str]
    awaiting_total: int
    wake: bool
    error: str | None


class RunRecord(RunReport):
    started_at: str
    finished_at: str | None


class SettingsStore(Protocol):
    def missing_settings(self, names: tuple[str, ...] = ..., /) -> list[str]: ...


def parse(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def run_record(row: sqlite3.Row | None) -> RunRecord | None:
    if row is None:
        return None
    report = cast(RunReport, json.loads(cast(str | None, row['report']) or '{}'))
    return {'started_at': cast(str, row['started_at']), 'finished_at': cast(str | None, row['finished_at']), **report}


class SearchState:
    def __init__(self, path: str | Path) -> None:
        self.path: Path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            for statement in SCHEMA:
                _ = connection.execute(statement)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def card(self, listing_id: str) -> CardRecord | None:
        with self.connect() as connection:
            row = cast(sqlite3.Row | None, connection.execute('SELECT signature, areas FROM cards WHERE listing_id = ?', (listing_id,)).fetchone())
            return {'signature': cast(str, row['signature']), 'areas': cast(list[str], json.loads(cast(str, row['areas'])))} if row else None

    def saw(self, listing_id: str, signature: str, areas: list[str], now: datetime) -> None:
        with self.connect() as connection:
            _ = connection.execute('''INSERT INTO cards VALUES (?, ?, ?, ?, ?) ON CONFLICT(listing_id)
                DO UPDATE SET signature = excluded.signature, areas = excluded.areas, last_seen = excluded.last_seen''',
                                   (listing_id, signature, json.dumps(areas), now.isoformat(), now.isoformat()))

    def first_seen(self) -> dict[str, datetime]:
        with self.connect() as connection:
            rows = cast(list[sqlite3.Row], connection.execute('SELECT * FROM cards').fetchall())
            return {cast(str, row['listing_id']): datetime.fromisoformat(cast(str, row['first_seen'])) for row in rows}

    def regions(self, site: str) -> dict[str, object]:
        with self.connect() as connection:
            row = cast(sqlite3.Row | None, connection.execute('SELECT regions FROM regions WHERE site = ?', (site,)).fetchone())
            return cast(dict[str, object], json.loads(cast(str, row['regions']))) if row else {}

    def save_regions(self, site: str, regions: Mapping[str, object]) -> None:
        with self.connect() as connection:
            _ = connection.execute('INSERT INTO regions VALUES (?, ?) ON CONFLICT(site) DO UPDATE SET regions = excluded.regions',
                                   (site, json.dumps(regions)))

    def blocked_until(self, site: str) -> datetime | None:
        with self.connect() as connection:
            row = cast(sqlite3.Row | None, connection.execute('SELECT until FROM blocks WHERE site = ?', (site,)).fetchone())
            return parse(cast(str, row['until'])) if row else None

    def block(self, site: str, now: datetime) -> datetime:
        with self.connect() as connection:
            row = cast(sqlite3.Row | None, connection.execute('SELECT count FROM blocks WHERE site = ?', (site,)).fetchone())
            count = (cast(int, row['count']) if row else 0) + 1
            until = now + min(FIRST_BLOCK * 2 ** (count - 1), LONGEST_BLOCK)
            _ = connection.execute('INSERT INTO blocks VALUES (?, ?, ?) ON CONFLICT(site) DO UPDATE SET count = excluded.count, until = excluded.until',
                                   (site, count, until.isoformat()))
            return until

    def unblock(self, site: str) -> None:
        with self.connect() as connection:
            _ = connection.execute('DELETE FROM blocks WHERE site = ?', (site,))

    def start_run(self, now: datetime) -> int:
        with self.connect() as connection:
            return cast(int, connection.execute('INSERT INTO runs (started_at) VALUES (?)', (now.isoformat(),)).lastrowid)

    def finish_run(self, run_id: int, now: datetime, report: RunReport) -> None:
        with self.connect() as connection:
            _ = connection.execute('UPDATE runs SET finished_at = ?, report = ? WHERE id = ?', (now.isoformat(), json.dumps(report), run_id))

    def latest_run(self) -> RunRecord | None:
        """The run started last, as it recorded itself: started_at, and once it finished, finished_at and its report. A run
        that never finished, because its process was killed, stays unfinished until a later run supersedes it."""
        with self.connect() as connection:
            return run_record(cast(sqlite3.Row | None, connection.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()))

    def running_since(self) -> datetime | None:
        latest = self.latest_run()
        return parse(latest['started_at']) if latest and not latest['finished_at'] else None

    def last_run(self) -> RunRecord | None:
        with self.connect() as connection:
            return run_record(cast(sqlite3.Row | None, connection.execute('SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()))

    def has_searched(self) -> bool:
        with self.connect() as connection:
            return connection.execute("SELECT 1 FROM runs WHERE json_array_length(report, '$.sites') > 0 LIMIT 1").fetchone() is not None

    def missing_settings(self, store: SettingsStore) -> list[str]:
        """The settings the search waits for: the whole interview before its first search, afterwards only the settings
        it reads, so questions the interview gains later never stop a running search."""
        return store.missing_settings(SEARCH_SETTINGS) if self.has_searched() else store.missing_settings()

    def reported_at(self) -> datetime:
        with self.connect() as connection:
            row = cast(sqlite3.Row | None, connection.execute('SELECT at FROM reported WHERE id = 1').fetchone())
            return datetime.fromisoformat(cast(str, row['at'])) if row else datetime.min.replace(tzinfo=timezone.utc)

    def mark_reported(self, now: datetime) -> None:
        with self.connect() as connection:
            _ = connection.execute('INSERT INTO reported VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET at = excluded.at', (now.isoformat(),))

    def problem(self) -> str | None:
        """A problem the cron script found before the search could run, such as a missing environment."""
        with self.connect() as connection:
            row = cast(sqlite3.Row | None, connection.execute('SELECT text FROM problem WHERE id = 1').fetchone())
            return cast(str, row['text']) if row else None

    def set_problem(self, text: str | None) -> None:
        with self.connect() as connection:
            if text:
                _ = connection.execute('INSERT INTO problem VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET text = excluded.text', (text,))
            else:
                _ = connection.execute('DELETE FROM problem')
