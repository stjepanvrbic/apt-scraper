import json
import sqlite3
from collections.abc import Iterable
from typing import NotRequired, TypedDict, cast

from code_features import RankingFeatures
from shapes import Agent, Criteria, Listing, ListingStatus, LocationPriorities, Place, Preferences, RentalPeriod, Settings, ViewingAvailability


RANKING_EXCLUSION = """CASE
    WHEN l.status != 'available' THEN 'Not ranked: unavailable.'
    WHEN COALESCE(f.dismissed, 0) != 0 THEN 'Not ranked: dismissed.'
    WHEN l.matches_criteria != 1 THEN 'Not ranked: outside the criteria.'
END"""
RANKABLE = f'({RANKING_EXCLUSION}) IS NULL'


class ListingSummary(TypedDict):
    """The fields of a listing a gallery page shows."""
    id: str
    title: str | None
    price: int | float | None
    price_note: str | None
    currency: str | None
    rental_period: RentalPeriod | None
    size: str | None
    rooms: str | None
    address: str | None
    status: ListingStatus
    first_seen: str


class ContactSummary(TypedDict):
    status: str
    channel: str | None
    error: str | None


class ViewingSummary(TypedDict):
    id: str
    time: str
    details: str


class BrowseItem(ListingSummary):
    """A listing on a gallery page, with its rank, the user's actions on it and its latest correspondence."""
    content_fingerprint: str
    photos: list[str]
    rank: int | None
    reason: object
    exploration: bool
    analysis_current: bool
    favorite: bool
    dismissed: bool
    contact: ContactSummary | None
    correspondence: list[dict[str, object]]
    correspondence_total: int
    viewing: ViewingSummary | None
    unranked_reason: NotRequired[str | None]


class Page(TypedDict):
    items: list[BrowseItem]
    total: int
    page: int
    page_size: int


class SnapshotListing(Listing):
    """A listing with everything the store holds about it, as the gallery's state."""
    content_fingerprint: str
    matches_criteria: bool
    favorite: bool
    dismissed: bool
    rank: int | None
    reason: object
    features: RankingFeatures | None
    contact: dict[str, object] | None
    notes: list[dict[str, object]]
    correspondence: list[dict[str, object]]
    viewings: list[dict[str, object]]
    user_notes: bool
    paired: bool
    analysis_current: NotRequired[bool]


class Snapshot(TypedDict):
    listings: list[SnapshotListing]
    settings: Settings


def validate_page(page: object, page_size: object) -> None:
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError('Page must be positive and page_size must be between 1 and 100.')


def browse(connection: sqlite3.Connection, view: str, page: int, page_size: int) -> Page:
    validate_page(page, page_size)
    conditions = {
        'Rankings': RANKABLE,
        'Favorites': 'f.favorite = 1 AND f.dismissed = 0',
        'Contacted': 'c.listing_id IS NOT NULL',
        'Viewings': "v.status = 'booked'",
        'Dismissed': 'f.dismissed = 1',
    }
    if view not in conditions:
        raise ValueError('Unknown gallery view.')
    joins = 'FROM listings l LEFT JOIN ranking r ON r.listing_id = l.id LEFT JOIN feedback f ON f.listing_id = l.id LEFT JOIN contacts c ON c.listing_id = l.id'
    if view == 'Viewings':
        joins += ' JOIN viewings v ON v.listing_id = l.id'
    where = ' WHERE ' + ('' if view in ('Rankings', 'Contacted', 'Viewings') else 'l.matches_criteria = 1 AND ') + conditions[view]
    order = 'v.time, v.id' if view == 'Viewings' else 'CASE WHEN r.rank IS NOT NULL AND r.fingerprint = l.fingerprint THEN 0 ELSE 1 END, CASE WHEN r.fingerprint = l.fingerprint THEN r.rank END, l.id'
    fields = ['id', 'title', 'price', 'price_note', 'currency', 'rental_period', 'size', 'rooms', 'address', 'status', 'first_seen']
    summary = ', '.join(f"'{field}', json_extract(l.data, '$.{field}')" for field in fields)
    viewing = ', v.id AS viewing_id, v.time AS viewing_time, v.details AS viewing_details' if view == 'Viewings' else ''
    _ = connection.execute('BEGIN')
    total = cast(tuple[int], connection.execute('SELECT COUNT(*) ' + joins + where).fetchone())[0]
    rows = cast(list[sqlite3.Row], connection.execute(f"""SELECT json_object({summary}) AS data, json_extract(l.data, '$.photos[0]') AS photo,
        l.fingerprint AS content_fingerprint, r.rank, r.reason, COALESCE(r.exploration, 0) AS exploration, r.fingerprint = l.fingerprint AS analysis_current,
        COALESCE(f.favorite, 0) AS favorite, COALESCE(f.dismissed, 0) AS dismissed,
        c.status AS contact_status, c.channel AS contact_channel, c.error AS contact_error {viewing}
        {joins} {where} ORDER BY {order} LIMIT ? OFFSET ?""", (page_size, (page - 1) * page_size)).fetchall())
    items: list[BrowseItem] = []
    for row in rows:
        photo, reason, contact_status = cast(str | None, row['photo']), cast(str | None, row['reason']), cast(str | None, row['contact_status'])
        item: BrowseItem = {
            **cast(ListingSummary, json.loads(cast(str, row['data']))),
            'content_fingerprint': cast(str, row['content_fingerprint']), 'photos': [photo] if photo else [],
            'rank': cast(int | None, row['rank']), 'reason': cast(object, json.loads(reason)) if reason else None,
            'exploration': bool(cast(int, row['exploration'])), 'analysis_current': bool(cast(int | None, row['analysis_current'])),
            'favorite': bool(cast(int, row['favorite'])), 'dismissed': bool(cast(int, row['dismissed'])),
            'contact': {'status': contact_status, 'channel': cast(str | None, row['contact_channel']),
                        'error': cast(str | None, row['contact_error'])} if contact_status else None,
            'correspondence': [], 'correspondence_total': 0, 'viewing': None,
        }
        if view == 'Viewings':
            item['viewing'] = {'id': cast(str, row['viewing_id']), 'time': cast(str, row['viewing_time']),
                               'details': cast(str, row['viewing_details'])}
        items.append(item)
    if view == 'Contacted' and items:
        placeholders = ','.join('?' for _ in items)
        messages = cast(Iterable[sqlite3.Row], connection.execute(f"""SELECT * FROM (
            SELECT id, listing_id, direction, channel, substr(text, 1, 500) AS text, created_at,
            COUNT(*) OVER (PARTITION BY listing_id) AS total,
            ROW_NUMBER() OVER (PARTITION BY listing_id ORDER BY id DESC) AS position
            FROM correspondence WHERE listing_id IN ({placeholders})
        ) WHERE position <= 3 ORDER BY id""", [item['id'] for item in items]))
        by_id = {item['id']: item for item in items}
        for message in messages:
            by_id[cast(str, message['listing_id'])]['correspondence'].append(cast(dict[str, object], dict(message)))
            by_id[cast(str, message['listing_id'])]['correspondence_total'] = cast(int, message['total'])
    return {'items': items, 'total': total, 'page': page, 'page_size': page_size}


def answered_pairs(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    """The user's pairwise answers as (chosen, other), oldest first."""
    return [(cast(str, row['chosen']), cast(str, row['other']))
            for row in cast(Iterable[sqlite3.Row], connection.execute('SELECT chosen, other FROM pairwise_answers ORDER BY id'))]


def paired(pairs: Iterable[tuple[str, str]]) -> set[str]:
    """The listings of the pairwise answers, either side."""
    return {identifier for pair in pairs for identifier in pair}


def user_noted(connection: sqlite3.Connection) -> set[str]:
    """The listings with a note from the user."""
    return {cast(str, row['listing_id'])
            for row in cast(Iterable[sqlite3.Row], connection.execute("SELECT DISTINCT listing_id FROM notes WHERE role = 'user'"))}


def rows(connection: sqlite3.Connection, query: str) -> Iterable[sqlite3.Row]:
    return cast(Iterable[sqlite3.Row], connection.execute(query))


def settings_of(rows: Iterable[sqlite3.Row]) -> Settings:
    """The settings rows as the settings they hold, each validated by its validator before it was stored."""
    values = {cast(str, row['name']): cast(str, row['value']) for row in rows}
    settings: Settings = {}
    if 'criteria' in values:
        settings['criteria'] = cast(Criteria, json.loads(values['criteria']))
    if 'preferences' in values:
        settings['preferences'] = cast(Preferences, json.loads(values['preferences']))
    if 'listing_sites' in values:
        settings['listing_sites'] = cast(list[str], json.loads(values['listing_sites']))
    if 'viewing_availability' in values:
        settings['viewing_availability'] = cast(ViewingAvailability, json.loads(values['viewing_availability']))
    if 'places' in values:
        settings['places'] = cast(list[Place], json.loads(values['places']))
    if 'location_priorities' in values:
        settings['location_priorities'] = cast(LocationPriorities, json.loads(values['location_priorities']))
    if 'transit_feed' in values:
        settings['transit_feed'] = cast(str, json.loads(values['transit_feed']))
    return settings


def snapshot(connection: sqlite3.Connection) -> Snapshot:
    _ = connection.execute('BEGIN')
    noted = user_noted(connection)
    compared = paired(answered_pairs(connection))
    listings: dict[str, SnapshotListing] = {}
    for row in rows(connection, 'SELECT * FROM listings'):
        identifier = cast(str, row['id'])
        listings[identifier] = {**cast(Listing, json.loads(cast(str, row['data']))), 'content_fingerprint': cast(str, row['fingerprint']),
                                'matches_criteria': bool(cast(int, row['matches_criteria'])),
                                'favorite': False, 'dismissed': False, 'rank': None, 'reason': None, 'features': None,
                                'contact': None, 'notes': [], 'correspondence': [], 'viewings': [],
                                'user_notes': identifier in noted, 'paired': identifier in compared}
    for row in rows(connection, 'SELECT * FROM ranking'):
        listing = listings[cast(str, row['listing_id'])]
        reason, features = cast(str | None, row['reason']), cast(str | None, row['features'])
        listing['rank'] = cast(int | None, row['rank'])
        listing['reason'] = cast(object, json.loads(reason)) if reason else None
        listing['features'] = cast(RankingFeatures, json.loads(features)) if features else None
        listing['analysis_current'] = cast(str, row['fingerprint']) == listing['content_fingerprint']
    for row in rows(connection, 'SELECT * FROM feedback'):
        listing = listings[cast(str, row['listing_id'])]
        listing['favorite'], listing['dismissed'] = bool(cast(int, row['favorite'])), bool(cast(int, row['dismissed']))
    for row in rows(connection, 'SELECT * FROM contacts'):
        listings[cast(str, row['listing_id'])]['contact'] = {**cast(dict[str, object], dict(row)), 'agent': cast(Agent, json.loads(cast(str, row['agent'])))}
    for table in ('notes', 'correspondence', 'viewings'):
        order = 'time, id' if table == 'viewings' else 'id'
        for row in rows(connection, f'SELECT * FROM {table} ORDER BY {order}'):
            listings[cast(str, row['listing_id'])][table].append(cast(dict[str, object], dict(row)))
    return {'listings': list(listings.values()), 'settings': settings_of(rows(connection, 'SELECT * FROM settings'))}
