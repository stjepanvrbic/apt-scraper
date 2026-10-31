import json
import math
import os
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Literal, NamedTuple, NotRequired, TypedDict, cast
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from chat import ChatMessage, ChatPrompt, MessageStatus, ToolEvent
from code_features import FEATURES, CodeFeatures, RankingFeatures, configured_transit_feed, store_all_code_features, store_code_features
from learning import encode, label_clock
from photo_catalog import MATCH_ROOMS, vocabulary as photo_vocabulary
from queries import RANKABLE, RANKING_EXCLUSION, BrowseItem, Page, Snapshot, answered_pairs, browse, paired, snapshot, user_noted, validate_page
from record import (SEED_TAGS, Judgment, NotShown, PhotoRoom, Rating, Tags, UnitLevel, Verdict, known_spelling, validate_extraction,
                    validate_judgments)
from schema import FINGERPRINT, LISTING_ID, MIGRATIONS, content_fingerprint, migrate
from scoring import (Direction, Explanation, FeatureReading, FeatureReason, Impression, NoteReading, PhotoInputs, RankingCheck, RankingInputs,
                     RankingListing, RankingResult, RankingSummary, Reason, ReasonText, Visit, compose_reason, reading_vocabulary, stated_phrases,
                     validate_note_reading, validate_preference_reading)
from shapes import Agent, Criteria, Listing, Settings, is_array, is_object, loads


class Unchanged(Enum):
    """The default of update_chat's prompt and error, which leaves the stored value as it is, since None clears it."""
    token = 0


UNCHANGED = Unchanged.token
CONTACT_CHANNELS = {'email', 'imessage', 'contact_form'}
RENTAL_PERIODS = ('day', 'week', 'month', 'year', 'total')
WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
VIEWING_MINUTES = 60
CRITERIA = ('location', 'price', 'size', 'rooms', 'move_in', 'other')
PREFERENCES = ('likes', 'dislikes')
PLACE_FIELDS = ('name', 'address')
IMPORTANCE = ('low', 'medium', 'high')
READ_ATTEMPTS = 3
ENCODER_MANIFEST = Path(__file__).resolve().parents[2] / 'ranking' / 'encoder' / 'model.json'
COMPARE_SURFACES = ('gateway', 'gallery', 'conversation')

CriteriaVerdict = Literal['inside', 'outside', 'pending']
Judge = Callable[[Criteria, RankingFeatures | None], CriteriaVerdict]
"""What a listing's current analysis says about the stored criteria: a judge decides it for apply_criteria."""
ContactStatus = Literal['pending', 'in_progress', 'contacted', 'failed', 'closed']
ContactChannel = Literal['email', 'imessage', 'contact_form']
MessageDirection = Literal['incoming', 'outgoing']
NoteRole = Literal['user', 'agent']
ViewingStatus = Literal['agreed', 'booked', 'canceled']
ReviewStatus = Literal['waiting', 'running', 'done', 'failed', 'superseded']
LearningKind = Literal['favorite', 'unfavorite', 'dismiss', 'restore', 'contact', 'pair', 'note']
SignalRow = tuple[str, str, str, str, str, str, int | None, str | None, int | None]
"""An implicit_signals row in column order: event, visit, listing, fingerprint, kind, time, duration, photo, rank."""
PhotoSimilarity = tuple[str, str, str, float]
"""The best-match similarity of two listings' photos of one room: (room, listing, other listing, value)."""


class UpsertResult(TypedDict):
    changed: bool
    needs_analysis: bool
    content_fingerprint: str


class Geocode(TypedDict):
    """The coordinates a geocoder found for a place address, and how it named the location."""
    latitude: float
    longitude: float
    found: str


class Station(TypedDict):
    id: str
    name: str
    latitude: float
    longitude: float


class TransitFeed(TypedDict):
    url: str
    fetched_at: str | None
    stations: list[Station]


class StoredCodeFeatures(CodeFeatures):
    """A listing's code features as ListingStore.code_features reads them: with the content they were computed for, when
    the listing was first seen and how many days ago that was."""
    fingerprint: str
    first_seen: str
    days_since_first_seen: int


class RecordPhoto(PhotoRoom):
    url: str


class StoredExtraction(Tags):
    """The model's part of a feature record as the store keeps it: validate_extraction's record without the judgments of
    the stated preferences, which the feature_records table keeps apart, and with each photo's URL."""
    photos: list[RecordPhoto]
    ratings: dict[str, Rating | NotShown]
    floor: str
    style: str
    outdoor_space: str
    building_type: str
    unit_level: UnitLevel
    flags: dict[str, bool | None]
    summary: str


class FeatureRecord(TypedDict):
    extraction: StoredExtraction
    preferences: dict[str, Judgment]
    extracted_at: str


class SavedExtraction(TypedDict):
    new_tags: list[str]


class ReviewRecord(TypedDict):
    """A rerank's review pass: its state, how many times it was handed to the agent, when its state last changed and its
    result, the saved review or the error that ended it, once it has one."""
    state: ReviewStatus
    attempts: int
    updated_at: str
    result: object


class RerankSummary(RankingSummary):
    """A reranks row's result: the rerank's summary and the ids left out because their listing changed meanwhile."""
    stale: list[str]


class RerankRecord(RerankSummary):
    id: int
    finished_at: str
    review: ReviewRecord | None


class RankedEntry(TypedDict):
    rank: int
    reason: Reason
    explanation: Explanation
    exploration: bool


class UnreadNote(TypedDict):
    id: int
    listing_id: str
    text: str
    created_at: str


class NoteRecord(TypedDict):
    id: int
    listing_id: str
    role: NoteRole
    text: str
    created_at: str


class ContactRecord(TypedDict):
    """A contacts row, its agent parsed."""
    listing_id: str
    status: ContactStatus
    requested_at: str
    channel: ContactChannel | None
    agent: Agent
    error: str | None
    handled_through: int
    subject: str | None


class DueContact(TypedDict):
    listing_id: str
    reason: Literal['reply', 'answer', 'start']


class QuestionRecord(TypedDict):
    id: int
    listing_id: str
    question: str
    field: str | None
    purpose: str | None
    asked_at: str
    external_message_id: str | None
    answer: str | None
    answered_at: str | None
    handled: int


class ApprovalRecord(TypedDict):
    id: int
    listing_id: str
    field: str
    value: str
    purpose: str
    approved_at: str
    revoked_at: str | None


class ViewingRecord(TypedDict):
    id: str
    listing_id: str
    time: str
    details: str
    calendar_event_id: str | None
    status: ViewingStatus


class CorrespondenceRecord(TypedDict):
    id: int
    listing_id: str
    direction: MessageDirection
    channel: ContactChannel
    text: str
    created_at: str
    external_message_id: str
    external_thread_id: str | None


class ListingDetail(Listing):
    """A listing with everything the store holds about it, as ListingStore.listing reads it: its notes, correspondence
    and viewings one page each, with the count of all of them."""
    matches_criteria: bool
    rank: int | None
    reason: Reason | None
    explanation: Explanation | None
    exploration: bool
    content_fingerprint: str
    analysis_current: bool
    favorite: bool
    dismissed: bool
    contact: ContactRecord | None
    notes_total: int
    notes: list[NoteRecord]
    correspondence_total: int
    correspondence: list[CorrespondenceRecord]
    viewings_total: int
    viewings: list[ViewingRecord]
    unranked_reason: NotRequired[str | None]


class ChatState(TypedDict):
    messages: list[ChatMessage]
    total: int
    active: bool
    session_id: str | None


class CompareFailure(TypedDict):
    at: str
    reason: str


class CompareInvitation(TypedDict):
    due: bool
    delivered: dict[str, str]
    failed: CompareFailure | None


class EncoderQuality(TypedDict):
    passed: bool
    room_passed: bool


class EncoderManifest(TypedDict):
    """The part of the shipped photo encoder's model.json the store reads; the encoder reads the rest."""
    identity: str
    quality: EncoderQuality


class BlindTestResult(TypedDict):
    """The part of a finished blind test's result the store reads (ranking/scripts/blind_test.py writes it)."""
    passed: bool


class EncoderState(TypedDict):
    model: str | None
    quality_passed: bool
    room_passed: bool
    blind_test: BlindTestResult | None
    enabled: bool


class PhotoTarget(TypedDict):
    id: str
    fingerprint: str
    labeled: bool
    first_seen: str
    photos: list[str]
    rooms: dict[str, str] | None


class PhotoFeatures(TypedDict):
    """The photo encoder's results for one listing, as save_photo_features takes them: the content they were computed
    for, the best photo's match with each probe, the encoder's room type of each photo and whether extraction's room
    types disagree with it on most photos."""
    fingerprint: str
    probes: dict[str, float]
    rooms: list[str]
    reread: bool


class TimeZoneDataMissing(RuntimeError):
    pass


class ViewingConflict(ValueError):
    pass


def select(connection: sqlite3.Connection, query: str, parameters: Sequence[object] = ()) -> Iterable[sqlite3.Row]:
    """The rows of a query on a connection whose row factory is sqlite3.Row."""
    return cast(Iterable[sqlite3.Row], connection.execute(query, parameters))


def select_one(connection: sqlite3.Connection, query: str, parameters: Sequence[object] = ()) -> sqlite3.Row | None:
    return cast(sqlite3.Row | None, connection.execute(query, parameters).fetchone())


def single(connection: sqlite3.Connection, query: str, parameters: Sequence[object] = ()) -> sqlite3.Row:
    """The row of a query that always has one, such as a count or a pragma."""
    return cast(sqlite3.Row, connection.execute(query, parameters).fetchone())


def inserted(cursor: sqlite3.Cursor) -> int:
    """The id of the row an INSERT just added."""
    return cast(int, cursor.lastrowid)


def settings_of(rows: Iterable[sqlite3.Row]) -> Settings:
    """The settings rows as the settings they hold, which validate_settings checked before they were stored."""
    return cast(Settings, {cast(str, row['name']): loads(cast(str, row['value'])) for row in rows})


def contact_record(row: sqlite3.Row) -> ContactRecord:
    return cast(ContactRecord, {**dict(row), 'agent': loads(cast(str, row['agent']))})


def require_text(value: object, name: str, *, limit: int = 20000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} must contain 1 to {limit} characters.')
    return value.strip()


def require_url(value: object) -> None:
    _ = require_text(value, 'URL')
    parsed = urlsplit(cast(str, value))
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('URLs must use http or https without credentials.')


def validate_listing(listing: object) -> Listing:
    fields = {'id', 'title', 'price', 'currency', 'size', 'rooms', 'address', 'description',
              'amenities', 'photos', 'sources', 'first_seen', 'status', 'rental_period', 'agent', 'price_note'}
    if not is_object(listing) or not {'id', 'sources'} <= set(listing) or not set(listing) <= fields:
        raise ValueError('A listing needs id and sources, with only documented listing fields.')
    record: dict[str, object] = {**dict.fromkeys(fields), 'amenities': [], 'photos': [], 'agent': {},
                                 'first_seen': datetime.now(timezone.utc).date().isoformat(), 'status': 'available', **listing}
    for field in fields - {'price', 'agent', 'amenities', 'photos', 'sources'}:
        value = record[field]
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f'{field} must be text or null.')
            record[field] = value.strip() or None
    identifier = require_text(record['id'], 'id')
    if not re.fullmatch(LISTING_ID, identifier):
        raise ValueError('Listing id must use letters, digits, colons, dots, underscores or hyphens.')
    price = record['price']
    if isinstance(price, str):
        record['price_note'] = require_text(price, 'Price description', limit=1000)
        record['price'] = price = None
    if price is not None and (not isinstance(price, (int, float)) or type(price) not in (int, float) or not math.isfinite(price) or price < 0):
        raise ValueError('Price must be a finite nonnegative number.')
    if record['rental_period'] not in (None, *RENTAL_PERIODS):
        raise ValueError('Rental period must be day, week, month, year or total.')
    validate_agent(record['agent'])
    currency = record['currency']
    if currency is not None and (not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency)):
        raise ValueError('Currency must be a three-letter currency code.')
    if record['status'] not in ('available', 'unavailable'):
        raise ValueError('Status must be available or unavailable.')
    first_seen = record['first_seen']
    if not isinstance(first_seen, str) or date.fromisoformat(first_seen).isoformat() != first_seen:
        raise ValueError('First seen must use YYYY-MM-DD.')
    arrays: dict[str, list[object]] = {}
    for field in ('amenities', 'photos', 'sources'):
        value = record[field]
        if not is_array(value):
            raise ValueError(f'{field} must be an array.')
        arrays[field] = value
    for amenity in arrays['amenities']:
        _ = require_text(amenity, 'Amenity')
    for photo in arrays['photos']:
        if not isinstance(photo, str) or not re.fullmatch(r'/media/[A-Za-z0-9._-]+', photo):
            require_url(photo)
    if not arrays['sources']:
        raise ValueError('At least one source is required.')
    for source in arrays['sources']:
        if not is_object(source) or set(source) != {'label', 'url'}:
            raise ValueError('A source needs a label and URL.')
        _ = require_text(source['label'], 'Source label')
        require_url(source['url'])
    return cast(Listing, record)


def require_object(value: object, name: str, keys: Iterable[str]) -> dict[str, object]:
    keys = tuple(keys)
    if not is_object(value) or not set(value) <= set(keys):
        raise ValueError(f'{name} must be an object with only {", ".join(keys)}.')
    return value


def require_texts(value: object, name: str) -> None:
    if not is_array(value):
        raise ValueError(f'{name} must be an array of text.')
    for item in value:
        _ = require_text(item, name, limit=1000)


def require_date(value: object, name: str) -> str:
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError(f'{name} must use YYYY-MM-DD.')
    return value


def require_range(value: object, name: str, units: tuple[str, ...] = ()) -> tuple[dict[str, object], bool]:
    """The range's fields, and whether either bound limits anything."""
    limits = require_object(value, name, ('min', 'max', *units))
    bounds: list[int | float | None] = []
    for key in ('min', 'max'):
        bound = limits.get(key)
        if bound is not None and (not isinstance(bound, (int, float)) or type(bound) not in (int, float) or not math.isfinite(bound) or bound < 0):
            raise ValueError(f'{name} limits must be nonnegative numbers or null.')
        bounds.append(bound)
    low, high = bounds
    if low is not None and high is not None and low > high:
        raise ValueError(f'{name} minimum exceeds its maximum.')
    return limits, bounds != [None, None]


def validate_criteria(criteria: object) -> None:
    fields = require_object(criteria, 'Criteria', CRITERIA)
    if 'location' in fields:
        location = fields['location']
        require_texts(location, 'Location')
        if not location:
            raise ValueError('Location needs at least one area.')
    if 'price' in fields:
        price, limited = require_range(fields['price'], 'Price', ('currency', 'period'))
        currency = price.get('currency')
        if (limited or 'currency' in price) and not (isinstance(currency, str) and re.fullmatch(r'[A-Z]{3}', currency)):
            raise ValueError('A price limit needs a three-letter currency code.')
        if (limited or 'period' in price) and price.get('period') not in RENTAL_PERIODS:
            raise ValueError(f'A price limit needs a period: {", ".join(RENTAL_PERIODS)}.')
    if 'size' in fields:
        size, limited = require_range(fields['size'], 'Size', ('unit',))
        if (limited or 'unit' in size) and size.get('unit') not in ('m2', 'sqft'):
            raise ValueError('A size limit needs the unit m2 or sqft.')
    if 'rooms' in fields:
        _ = require_range(fields['rooms'], 'Rooms')
    if fields.get('move_in') is not None:
        _ = require_date(fields['move_in'], 'Move-in date')
    if 'other' in fields:
        require_texts(fields['other'], 'Other criteria')


def validate_preferences(preferences: object) -> None:
    for key, value in require_object(preferences, 'Preferences', PREFERENCES).items():
        require_texts(value, key.capitalize())


def validate_listing_sites(sites: object) -> None:
    if not is_array(sites):
        raise ValueError('Listing sites must be an array of URLs.')
    for url in sites:
        require_url(url)
    if len(set(sites)) != len(sites):
        raise ValueError('Each listing site is named once.')


def validate_viewing_availability(availability: object) -> None:
    fields = require_object(availability, 'Viewing availability', ('timezone', 'weekly', 'unavailable'))
    if 'timezone' in fields:
        name = fields['timezone']
        try:
            # A time zone that is not text fails the same way as one no database knows.
            if not isinstance(name, str):
                raise TypeError('Timezone must be text.')
            _ = ZoneInfo(name)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            if not available_timezones():
                raise TimeZoneDataMissing(f'{sys.executable} has no time zone database, so the time zone cannot be checked. '
                                          'Run the script with a Python that has the tzdata package, such as the one Hermes runs on.') from None
            raise ValueError('Timezone must be an IANA time zone such as Europe/Berlin.') from None
    weekly, unavailable = fields.get('weekly', []), fields.get('unavailable', [])
    if not is_array(weekly) or not is_array(unavailable):
        raise ValueError('Weekly and unavailable must be arrays.')
    for window in weekly:
        if not is_object(window) or set(window) != {'day', 'start', 'end'} or window['day'] not in WEEKDAYS:
            raise ValueError(f'A weekly window needs a day ({", ".join(WEEKDAYS)}), a start and an end.')
        start, end = window['start'], window['end']
        if not (isinstance(start, str) and isinstance(end, str) and re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', start)
                and re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', end)):
            raise ValueError('Window times use 24-hour HH:MM.')
        if start >= end:
            raise ValueError('A window must end after it starts.')
    for period in unavailable:
        if not is_object(period) or set(period) != {'from', 'to'}:
            raise ValueError('An unavailable period needs from and to dates.')
        start = require_date(period['from'], 'Unavailable from')
        end = require_date(period['to'], 'Unavailable to')
        if start > end:
            raise ValueError('An unavailable period must end on or after its start.')


def validate_places(places: object) -> None:
    if not is_array(places):
        raise ValueError('Places must be an array.')
    names: list[str] = []
    for place in places:
        if not is_object(place) or set(place) != set(PLACE_FIELDS):
            raise ValueError(f'A place has exactly {", ".join(PLACE_FIELDS)}.')
        names.append(require_text(place['name'], 'Place name', limit=200).casefold())
        _ = require_text(place['address'], 'Place address', limit=1000)
    if len(set(names)) != len(places):
        raise ValueError('Each place is named once.')


def require_importance(value: object, name: str) -> None:
    if not isinstance(value, str) or value not in IMPORTANCE:
        raise ValueError(f'The importance of {name} is {", ".join(IMPORTANCE)}.')


def validate_location_priorities(priorities: object) -> None:
    fields = require_object(priorities, 'Location priorities', ('places', 'rapid_transit', 'areas'))
    for key in ('places', 'rapid_transit'):
        if key in fields:
            require_importance(fields[key], key)
    areas = fields.get('areas', {})
    if not is_object(areas):
        raise ValueError('Location priority areas must be an object from each area of the criteria to its importance.')
    for area, importance in areas.items():
        require_importance(importance, area)


def reconcile_area_priorities(saved: Settings, changed: Mapping[str, object]) -> Settings:
    """The settings to save along with changed: the stored location priorities without the areas the criteria no longer name.
    saved holds every setting after the change. A changed priority for an area the criteria do not name is rejected."""
    priorities = saved.get('location_priorities')
    if priorities is None:
        return {}
    areas = saved.get('criteria', {}).get('location', [])
    unknown = [area for area in priorities.get('areas', {}) if area not in areas]
    if 'location_priorities' in changed and unknown:
        raise ValueError(f'Location priorities name areas the criteria do not: {", ".join(unknown)}.')
    if not unknown:
        return {}
    return {'location_priorities': {**priorities, 'areas': {area: importance for area, importance in priorities['areas'].items() if area in areas}}}


SETTINGS: dict[str, Callable[[object], None]] = {
    'criteria': validate_criteria,
    'preferences': validate_preferences,
    'listing_sites': validate_listing_sites,
    'viewing_availability': validate_viewing_availability,
    'places': validate_places,
    'location_priorities': validate_location_priorities,
    'transit_feed': require_url,
}


def store_path(sample: bool = False) -> Path:
    if os.environ.get('HERMES_HOME'):
        directory = Path(os.environ['HERMES_HOME']) / 'workspace' / 'gallery'
    elif sys.platform == 'win32':
        directory = Path(os.environ['LOCALAPPDATA']) / 'apt-scraper'
    else:
        directory = Path.home() / 'Library' / 'Application Support' / 'apt-scraper'
    return directory / ('sample.sqlite3' if sample else 'listings.sqlite3')


def viewing_time(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError('Viewing time must be an ISO 8601 date and time with its UTC offset.')
    try:
        time = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError('Viewing time must be an ISO 8601 date and time with its UTC offset.') from None
    if time.utcoffset() is None:
        raise ValueError('Viewing time must include its UTC offset.')
    return time.astimezone(timezone.utc).isoformat()


def validate_agent(agent: object) -> None:
    if not is_object(agent) or not set(agent) <= {'name', 'email', 'phone', 'contact_form'}:
        raise ValueError('Agent accepts name, email, phone and contact_form.')
    for name, value in agent.items():
        _ = require_text(value, name, limit=1000)
        if name == 'contact_form':
            require_url(value)


def encoder_manifest(path: str | Path | None = None) -> EncoderManifest | None:
    """The shipped photo encoder's manifest, or None when the profile has none: its identity, files, preprocessing and
    the quality it measured before shipping."""
    path = Path(path or ENCODER_MANIFEST)
    return cast(EncoderManifest, json.loads(path.read_text(encoding='utf-8'))) if path.is_file() else None


def without_photo_features[K](readings: Mapping[K, Iterable[FeatureReading]]) -> dict[K, list[FeatureReading]]:
    """The preference readings as the ranking uses them while it has no photo columns: a reading that named only photo
    features names nothing, so its preference is judged per listing as one no feature expresses."""
    return {key: [item for item in features if not item['feature'].startswith('photo:')] for key, features in readings.items()}


def extraction_read(listing_id: str, fingerprint: str) -> str:
    """The model_reads key of the extraction of one listing content."""
    return json.dumps(['extraction', listing_id, fingerprint])


def explain_pending_rankings(connection: sqlite3.Connection, listings: Sequence[BrowseItem | ListingDetail]) -> None:
    """Sets unranked_reason, why a listing has no current rank, on each listing, None for a ranked one, and clears the
    rank, reason and exploration mark of the listings without a current rank."""
    for listing in listings:
        listing['unranked_reason'] = None
    pending = {listing['id']: listing for listing in listings if not listing['analysis_current'] or listing['rank'] is None}
    if not pending:
        return
    placeholders = ','.join('?' for _ in pending)
    rows = list(select(connection, f'''SELECT l.id, l.fingerprint, records.listing_id AS record,
        {RANKING_EXCLUSION} AS exclusion FROM listings l
        LEFT JOIN feedback f ON f.listing_id = l.id
        LEFT JOIN feature_records records ON records.listing_id = l.id AND records.fingerprint = l.fingerprint
        WHERE l.id IN ({placeholders})''', list(pending)))
    reads = [extraction_read(cast(str, row['id']), cast(str, row['fingerprint'])) for row in rows]
    attempts = {cast(str, row[0]): cast(int, row[1])
                for row in select(connection, f'SELECT read, attempts FROM model_reads WHERE read IN ({placeholders})', reads)}
    for row, read in zip(rows, reads):
        listing = pending[cast(str, row['id'])]
        exclusion = cast(str | None, row['exclusion'])
        if exclusion:
            reason = exclusion
        elif cast(str | None, row['record']):
            reason = 'Not ranked yet: new or changed since the last rerank.'
        elif attempts.get(read, 0) >= READ_ATTEMPTS:
            reason = f'Not ranked yet: extraction failed {READ_ATTEMPTS} times. Score and rerank to use code-derived features.'
        else:
            reason = 'Not ranked yet: waiting for its feature record.'
        listing['rank'] = None
        listing['reason'] = None
        listing['unranked_reason'] = reason
        listing['exploration'] = False


class CriteriaApplied(NamedTuple):
    """The count of each verdict over every stored listing, and the ids, in store order, of the listings this application
    of the criteria changed: shown ones it put outside (hidden) or left waiting for a judgment (awaiting), and hidden ones
    it showed (shown)."""
    verdicts: dict[CriteriaVerdict, int]
    hidden: list[str]
    awaiting: list[str]
    shown: list[str]


def apply_criteria(connection: sqlite3.Connection, judge: Judge) -> CriteriaApplied | None:
    row = select_one(connection, "SELECT value FROM settings WHERE name = 'criteria'")
    if not row:
        return None
    criteria = cast(Criteria, loads(cast(str, row['value'])))
    rows = [(cast(str, row['id']), bool(cast(int, row['matches_criteria'])), cast(str | None, row['features']))
            for row in select(connection, 'SELECT l.id, l.matches_criteria, CASE WHEN r.fingerprint = l.fingerprint THEN r.features END AS features '
                              + 'FROM listings l LEFT JOIN ranking r ON r.listing_id = l.id ORDER BY l.rowid')]
    verdicts = {identifier: judge(criteria, cast(RankingFeatures, loads(features)) if features else None) for identifier, _, features in rows}
    flipped = [identifier for identifier, matches, _ in rows if (verdicts[identifier] == 'inside') != matches]
    _ = connection.executemany('UPDATE listings SET matches_criteria = ? WHERE id = ?', [(verdicts[identifier] == 'inside', identifier) for identifier in flipped])
    changed = {verdict: [identifier for identifier in flipped if verdicts[identifier] == verdict] for verdict in ('outside', 'pending', 'inside')}
    return CriteriaApplied(dict(Counter(verdicts.values())), changed['outside'], changed['pending'], changed['inside'])


class ListingStore:
    def __init__(self, path: str | Path) -> None:
        self.path: Path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as connection:
            migrate(connection)

    @property
    def schema_version(self) -> int:
        with self._open() as connection:
            return cast(int, single(connection, 'PRAGMA user_version')[0])

    @contextmanager
    def _open(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        _ = connection.execute('PRAGMA foreign_keys = ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def connect(self, write: bool = False) -> Generator[sqlite3.Connection]:
        with self._open() as connection:
            if write:
                _ = connection.execute('BEGIN IMMEDIATE')
            # A process that opened the store before another process migrated it would write in a schema it does not know.
            if cast(int, single(connection, 'PRAGMA user_version')[0]) != len(MIGRATIONS):
                raise ValueError('This database was migrated to another schema after the gallery opened it. Open the gallery again.')
            before = connection.total_changes
            yield connection
            if write and connection.total_changes != before:
                _ = connection.execute('UPDATE changes SET revision = revision + 1 WHERE id = 1')

    def upsert_listing(self, listing: object, *, matches_criteria: object = None) -> UpsertResult:
        validated = validate_listing(listing)
        if matches_criteria is not None and type(matches_criteria) is not bool:
            raise ValueError('Criteria eligibility must be true or false.')
        with self.connect(write=True) as connection:
            previous = select_one(connection, 'SELECT * FROM listings WHERE id = ?', (validated['id'],))
            if previous:
                validated['first_seen'] = cast(str, previous['first_seen'])
            fingerprint = content_fingerprint(validated)
            changed = not previous or cast(str, previous['fingerprint']) != fingerprint
            analysis = select_one(connection, 'SELECT * FROM ranking WHERE listing_id = ?', (validated['id'],))
            needs_analysis = not analysis or cast(str, analysis['fingerprint']) != fingerprint or cast(str | None, analysis['features']) is None
            if not previous or changed or cast(str, previous['status']) != validated['status']:
                _ = connection.execute(
                    '''INSERT INTO listings (id, data, status, first_seen, fingerprint) VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET data = excluded.data, status = excluded.status, fingerprint = excluded.fingerprint''',
                    (validated['id'], json.dumps(validated, ensure_ascii=False), validated['status'], validated['first_seen'], fingerprint),
                )
            if changed:
                store_code_features(connection, validated['id'])
            if matches_criteria is not None:
                _ = connection.execute('UPDATE listings SET matches_criteria = ? WHERE id = ? AND matches_criteria != ?',
                                       (matches_criteria, validated['id'], matches_criteria))
            return {'changed': bool(changed), 'needs_analysis': needs_analysis, 'content_fingerprint': fingerprint}

    def settings(self) -> Settings:
        with self.connect() as connection:
            return settings_of(select(connection, 'SELECT * FROM settings'))

    def has_listings(self) -> bool:
        with self.connect() as connection:
            return select_one(connection, 'SELECT 1 FROM listings LIMIT 1') is not None

    def set_availability(self, listing_id: str, status: str) -> None:
        if status not in ('available', 'unavailable'):
            raise ValueError('Status must be available or unavailable.')
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            _ = connection.execute("UPDATE listings SET status = ?, data = json_set(data, '$.status', ?) WHERE id = ? AND status != ?",
                                   (status, status, listing_id, status))

    def set_matches_criteria(self, listing_id: str, matches: object) -> None:
        if type(matches) is not bool:
            raise ValueError('Criteria eligibility must be true or false.')
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            _ = connection.execute('UPDATE listings SET matches_criteria = ? WHERE id = ? AND matches_criteria != ?', (matches, listing_id, matches))

    def update_settings(self, *, judge: Judge | None = None, **settings: object) -> CriteriaApplied | None:
        """Saving criteria applies them to every stored listing in the same transaction, with judge as in apply_criteria.
        Saving places or the transit feed computes every listing's distances again, to the places with stored geocodes."""
        if not settings or not set(settings) <= set(SETTINGS):
            raise ValueError(f'Settings are {", ".join(SETTINGS)}, and at least one is required.')
        for name, value in settings.items():
            SETTINGS[name](value)
        if judge is not None and 'criteria' not in settings:
            raise ValueError('A judge is given only with the criteria it applies.')
        with self.connect(write=True) as connection:
            if 'criteria' in settings and judge is None and select_one(connection, 'SELECT 1 FROM listings LIMIT 1'):
                raise ValueError('Stored listings must be judged against new criteria. Give a judge.')
            saved = settings_of(select(connection, 'SELECT * FROM settings'))
            settings = {**settings, **reconcile_area_priorities(cast(Settings, {**saved, **settings}), settings)}
            _ = connection.executemany('INSERT INTO settings VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value',
                                       [(name, json.dumps(value, allow_nan=False)) for name, value in settings.items()])
            if {'places', 'transit_feed'} & set(settings):
                store_all_code_features(connection)
            return apply_criteria(connection, judge) if 'criteria' in settings and judge else None

    def apply_criteria(self, judge: Judge) -> CriteriaApplied | None:
        """Shows each listing that judge(criteria, features) finds 'inside' the stored criteria and hides every other one.
        features is the listing's current analysis or None. Returns a CriteriaApplied, or None without criteria."""
        with self.connect(write=True) as connection:
            return apply_criteria(connection, judge)

    def save_geocodes(self, geocodes: Mapping[str, Geocode]) -> None:
        """Keeps the coordinates a geocoder found for place addresses, by address: {latitude, longitude, found}, where
        found is how the geocoder named the location."""
        now = datetime.now(timezone.utc).isoformat()
        with self.connect(write=True) as connection:
            _ = connection.executemany('''INSERT INTO geocodes VALUES (?, ?, ?, ?, ?) ON CONFLICT(address) DO UPDATE SET
                latitude = excluded.latitude, longitude = excluded.longitude, found = excluded.found, geocoded_at = excluded.geocoded_at''',
                                       [(address, found['latitude'], found['longitude'], found['found'], now) for address, found in geocodes.items()])

    def geocodes(self, addresses: Iterable[str]) -> dict[str, Geocode]:
        """The stored coordinates of each of the addresses that has them: {latitude, longitude, found}, by address."""
        with self.connect() as connection:
            return {cast(str, row['address']): {'latitude': cast(float, row['latitude']), 'longitude': cast(float, row['longitude']),
                                                'found': cast(str, row['found'])}
                    for address in addresses for row in select(connection, 'SELECT * FROM geocodes WHERE address = ?', (address,))}

    def transit_feed(self) -> TransitFeed:
        """The rapid-transit feed the settings name, the default without one, and the stations fetched from it:
        {url, fetched_at, stations}, with fetched_at None and no stations until they are fetched."""
        with self.connect() as connection:
            url = configured_transit_feed(connection)
            fetched = select_one(connection, 'SELECT * FROM transit_feed WHERE url = ?', (url,))
            stations = [cast(Station, dict(row)) for row in select(connection, 'SELECT * FROM stations ORDER BY id')] if fetched else []
            return {'url': url, 'fetched_at': cast(str, fetched['fetched_at']) if fetched else None, 'stations': stations}

    def save_stations(self, url: str, stations: Iterable[Station]) -> None:
        """Replaces the stored rapid-transit stations with those fetched from the feed at url, each {id, name, latitude,
        longitude}, and computes every listing's distances again."""
        with self.connect(write=True) as connection:
            _ = connection.execute('DELETE FROM stations')
            _ = connection.executemany('INSERT INTO stations VALUES (?, ?, ?, ?)',
                                       [(station['id'], station['name'], station['latitude'], station['longitude']) for station in stations])
            _ = connection.execute('INSERT INTO transit_feed VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET url = excluded.url, fetched_at = excluded.fetched_at',
                                   (url, datetime.now(timezone.utc).isoformat()))
            store_all_code_features(connection)

    def places_without_coordinates(self) -> list[str]:
        """The names of the saved places whose address has no stored geocode, such as places saved before places were
        geocoded. They count for no listing until they are saved again."""
        places = self.settings().get('places', [])
        located = self.geocodes({place['address'] for place in places})
        return [place['name'] for place in places if place['address'] not in located]

    def missing_settings(self, names: Iterable[str] | None = None) -> list[str]:
        """What the interview still needs, limited to the settings in names, by default all of them."""
        names = tuple(SETTINGS) if names is None else tuple(names)
        saved = self.settings()
        missing = [f'criteria.{key}' for key in CRITERIA if key not in saved.get('criteria', {})]
        missing += [f'preferences.{key}' for key in PREFERENCES if key not in saved.get('preferences', {})]
        if not saved.get('listing_sites'):
            missing.append('listing_sites')
        availability = saved.get('viewing_availability', {})
        missing += [f'viewing_availability.{key}' for key, value in (('timezone', availability.get('timezone')), ('weekly', availability.get('weekly')))
                    if not value]
        priorities = saved.get('location_priorities', {})
        if 'places' not in saved:
            missing.append('places')
        elif saved['places'] and 'places' not in priorities:
            missing.append('location_priorities.places')
        if 'rapid_transit' not in priorities:
            missing.append('location_priorities.rapid_transit')
        if not set(saved.get('criteria', {}).get('location', [])) <= set(priorities.get('areas', {})):
            missing.append('location_priorities.areas')
        return [item for item in missing if item.split('.')[0] in names]

    def analyze(self, listing_id: str, fingerprint: str, features: object) -> None:
        if not is_object(features):
            raise ValueError('Features must be an object.')
        encoded = json.dumps(features, allow_nan=False)
        with self.connect(write=True) as connection:
            self.require_fingerprint(connection, listing_id, fingerprint)
            _ = connection.execute('''INSERT INTO ranking (listing_id, fingerprint, features) VALUES (?, ?, ?)
                ON CONFLICT(listing_id) DO UPDATE SET fingerprint = excluded.fingerprint, features = excluded.features,
                rank = CASE WHEN ranking.fingerprint = excluded.fingerprint THEN ranking.rank END,
                reason = CASE WHEN ranking.fingerprint = excluded.fingerprint THEN ranking.reason END,
                explanation = CASE WHEN ranking.fingerprint = excluded.fingerprint THEN ranking.explanation END,
                exploration = ranking.exploration AND ranking.fingerprint = excluded.fingerprint''', (listing_id, fingerprint, encoded))
            store_code_features(connection, listing_id)

    def features(self, listing_id: str) -> RankingFeatures | None:
        with self.connect() as connection:
            self.require_listing(connection, listing_id)
            row = select_one(connection, 'SELECT features FROM ranking JOIN listings ON listings.id = ranking.listing_id WHERE listing_id = ? AND ranking.fingerprint = listings.fingerprint', (listing_id,))
            features = cast(str | None, row['features']) if row else None
            return cast(RankingFeatures, loads(features)) if features else None

    def code_features(self, today: date | None = None) -> dict[str, StoredCodeFeatures]:
        """The code-derived features of every listing's current content, as code_features.py describes them, by listing id,
        with the content fingerprint, first_seen and days_since_first_seen counted to today, a date (by default today in UTC)."""
        today = today or datetime.now(timezone.utc).date()
        with self.connect() as connection:
            return {cast(str, row['listing_id']): cast(StoredCodeFeatures, {
                        **{name: cast(object, row[name]) for name in ('fingerprint', *FEATURES, 'first_seen')},
                        'place_distances_km': loads(cast(str, row['place_distances_km'])),
                        'days_since_first_seen': (today - date.fromisoformat(cast(str, row['first_seen']))).days})
                    for row in select(connection, '''SELECT c.*, l.first_seen FROM code_features c
                        JOIN listings l ON l.id = c.listing_id AND l.fingerprint = c.fingerprint ORDER BY c.listing_id''')}

    def ranking_inputs(self, *, include_signals: bool = False, connection: sqlite3.Connection | None = None,
                       photos: bool | None = None) -> RankingInputs:
        """Everything a rerank reads, from one snapshot of the store: each listing with its current feature record or
        None, its judgments of stated preferences, its code-derived features, the criteria areas whose search found it
        or None, whether extraction gave up on it, whether it is ranked (available, inside the criteria and not
        dismissed), its rank from the latest rerank when its content has not changed since, and the user's actions on it; the settings; the readings of the stated preferences by (kind,
        phrase) and of the user's notes by note id; each listing's latest note sentiment; the pairwise answers as
        (chosen, other); and the tag list. Completed visits are included only for the rerank's diagnostic check.
        With photos true, or None while the encoder is on (encoder_state), it adds the photo encoder's features of the
        listings whose current content have them: each one's probe matches, its best-match similarity by room to each
        favorite, and the probe of each live hypothesis a probe scores."""
        with self.connect() if connection is None else nullcontext(connection) as connection:
            if not connection.in_transaction:
                _ = connection.execute('BEGIN')
            settings = settings_of(select(connection, 'SELECT * FROM settings'))
            attempts = {cast(str, row['read']): cast(int, row['attempts']) for row in select(connection, 'SELECT * FROM model_reads')}
            records = {cast(str, row['listing_id']): row for row in select(
                connection, 'SELECT r.* FROM feature_records r JOIN listings l ON l.id = r.listing_id AND l.fingerprint = r.fingerprint')}
            code = {cast(str, row['listing_id']): cast(CodeFeatures, {**dict(row), 'place_distances_km': loads(cast(str, row['place_distances_km']))})
                    for row in select(connection, 'SELECT c.* FROM code_features c JOIN listings l ON l.id = c.listing_id AND l.fingerprint = c.fingerprint')}
            ranking = {cast(str, row['listing_id']): row for row in select(
                connection, 'SELECT r.* FROM ranking r JOIN listings l ON l.id = r.listing_id AND l.fingerprint = r.fingerprint')}
            facts = {identifier: cast(RankingFeatures, loads(features)).get('facts', {})
                     for identifier, row in ranking.items() if (features := cast(str | None, row['features']))}
            feedback = {cast(str, row['listing_id']): row for row in select(connection, 'SELECT * FROM feedback')}
            contacts = {cast(str, row['listing_id']) for row in select(connection, 'SELECT listing_id FROM contacts')}
            noted = user_noted(connection)
            note_readings = {cast(int, row['note_id']): (cast(str, row['listing_id']), cast(NoteReading, loads(cast(str, row['reading']))))
                             for row in select(connection, 'SELECT r.*, n.listing_id FROM note_readings r JOIN notes n ON n.id = r.note_id ORDER BY r.note_id')}
            preference_readings = {(cast(Direction, row['kind']), cast(str, row['phrase'])): cast(list[FeatureReading], loads(cast(str, row['features'])))
                                   for row in select(connection, 'SELECT * FROM preference_readings')}
            pairs = answered_pairs(connection)
            tags = sorted(self.known_tags(connection))
            visits: list[Visit] = []
            if include_signals:
                visits = [cast(Visit, dict(row)) for row in select(connection, '''SELECT s.listing_id, s.visit_id,
                    SUM(COALESCE(s.duration_ms, 0)) AS duration_ms FROM implicit_signals s
                    JOIN listings l ON l.id = s.listing_id AND l.fingerprint = s.fingerprint
                    GROUP BY s.listing_id, s.fingerprint, s.visit_id
                    HAVING SUM(s.kind = 'open') > 0 AND SUM(s.kind = 'close') > 0''')]
            compared = paired(pairs)
            hypotheses: dict[str, dict[str, Judgment]] = {}
            for judgment in select(connection, '''SELECT j.*, h.phrase FROM hypothesis_judgments j
                    JOIN hypotheses h ON h.id = j.hypothesis AND h.state = 'active'
                    JOIN listings l ON l.id = j.listing_id AND l.fingerprint = j.fingerprint'''):
                hypotheses.setdefault(cast(str, judgment['listing_id']), {})[cast(str, judgment['phrase'])] = {
                    'verdict': cast(Verdict, judgment['verdict']), 'evidence': cast(str, judgment['evidence'])}
            listings: list[RankingListing] = []
            for row in select(connection, f'''SELECT l.*, {RANKABLE} AS rankable FROM listings l
                    LEFT JOIN feedback f ON f.listing_id = l.id ORDER BY l.id'''):
                data, identifier = cast(Listing, loads(cast(str, row['data']))), cast(str, row['id'])
                fingerprint = cast(str, row['fingerprint'])
                record = records.get(identifier)
                choice = feedback.get(identifier)
                dismissed = bool(choice and cast(int, choice['dismissed']))
                listings.append({
                    'id': identifier, 'fingerprint': fingerprint, 'title': data.get('title'), 'address': data.get('address'),
                    'rankable': bool(cast(int, row['rankable'])),
                    'record': cast(StoredExtraction, loads(cast(str, record['extraction']))) if record else None,
                    'judgments': cast(dict[str, Judgment], loads(cast(str, record['preferences']))) if record else {},
                    'hypotheses': hypotheses.get(identifier, {}),
                    'code': code.get(identifier, {}),
                    'areas': facts[identifier].get('areas') if identifier in facts else None,
                    'given_up': not record and attempts.get(extraction_read(identifier, fingerprint), 0) >= READ_ATTEMPTS,
                    'rank': cast(int | None, ranking[identifier]['rank']) if identifier in ranking else None,
                    'favorite': bool(choice and cast(int, choice['favorite'])), 'dismissed': dismissed, 'contact': identifier in contacts,
                    'user_notes': identifier in noted, 'paired': identifier in compared,
                })
            clock = label_clock(connection, listings, pairs)
            encoder = self.encoder_state(connection)
            photo_inputs = None
            model = encoder['model']
            if model and (encoder['enabled'] if photos is None else photos):
                photo_inputs = self.photo_inputs(connection, model, feedback)
        if photo_inputs is None:
            preference_readings = without_photo_features(preference_readings)
        inputs: RankingInputs = {'listings': listings, 'settings': settings, 'preference_readings': preference_readings, **clock,
                                 'note_readings': {note: reading for note, (_, reading) in note_readings.items()},
                                 'sentiments': {listing_id: reading['sentiment'] for listing_id, reading in note_readings.values()}, 'pairs': pairs,
                                 'tags': tags, 'implicit_visits': visits}
        if photo_inputs:
            inputs['photos'] = photo_inputs
        return inputs

    @staticmethod
    def photo_inputs(connection: sqlite3.Connection, model: str, feedback: Mapping[str, sqlite3.Row]) -> PhotoInputs:
        current = {cast(str, row['listing_id']): cast(dict[str, float], loads(cast(str, row['probes']))) for row in select(
            connection, 'SELECT p.* FROM photo_features p JOIN listings l ON l.id = p.listing_id AND l.fingerprint = p.fingerprint WHERE p.model = ?', (model,))}
        favorites = sorted(identifier for identifier, row in feedback.items()
                           if cast(int, row['favorite']) and not cast(int, row['dismissed']) and identifier in current)
        similarity: dict[str, dict[str, dict[str, float]]] = {room: {} for room in MATCH_ROOMS}
        if favorites:
            for row in select(connection, f'''SELECT room, other_id, listing_id, value FROM photo_similarity
                    WHERE model = ? AND other_id IN ({", ".join("?" * len(favorites))})''', (model, *favorites)):
                listing_id, room = cast(str, row['listing_id']), cast(str, row['room'])
                if listing_id in current and room in similarity:
                    similarity[room].setdefault(listing_id, {})[cast(str, row['other_id'])] = cast(float, row['value'])
        hypotheses = {cast(str, row['phrase']): cast(str, row['probe']) for row in select(
            connection, "SELECT phrase, probe FROM hypotheses WHERE state = 'active' AND probe IS NOT NULL ORDER BY phrase")}
        return {'model': model, 'probes': current, 'similarity': similarity, 'hypotheses': hypotheses}

    def encoder_state(self, connection: sqlite3.Connection | None = None) -> EncoderState:
        """The photo encoder's gate: its model, whether its shipped quality measurements passed (room types and probes),
        the latest finished blind test of that model, and whether the encoder is on, which needs both passed."""
        if connection is None:
            with self.connect() as connection:
                return self.encoder_state(connection)
        manifest = encoder_manifest()
        if manifest is None:
            return {'model': None, 'quality_passed': False, 'room_passed': False, 'blind_test': None, 'enabled': False}
        row = select_one(connection, '''SELECT result FROM encoder_blind_tests WHERE model = ? AND result IS NOT NULL
            ORDER BY id DESC LIMIT 1''', (manifest['identity'],))
        blind = cast(BlindTestResult, loads(cast(str, row['result']))) if row else None
        quality = manifest['quality']
        return {'model': manifest['identity'], 'quality_passed': quality['passed'], 'room_passed': quality['room_passed'],
                'blind_test': blind, 'enabled': bool(quality['passed'] and blind and blind['passed'])}

    def photo_targets(self) -> list[PhotoTarget]:
        """The listings the photo encoder works for, those the ranking ranks or learns from, labeled first and then the
        longest known: each one's current fingerprint, its photo URLs and its record's room type by photo URL."""
        with self.connect() as connection:
            rows = list(select(connection, f'''SELECT l.id, l.fingerprint, l.data, l.first_seen, {RANKABLE} AS rankable,
                    f.favorite, f.dismissed, r.extraction FROM listings l LEFT JOIN feedback f ON f.listing_id = l.id
                    LEFT JOIN feature_records r ON r.listing_id = l.id AND r.fingerprint = l.fingerprint ORDER BY l.id'''))
            contacts = {cast(str, row['listing_id']) for row in select(connection, 'SELECT listing_id FROM contacts')}
            noted, compared = user_noted(connection), paired(answered_pairs(connection))
        targets: list[PhotoTarget] = []
        for row in rows:
            identifier = cast(str, row['id'])
            labeled = bool(cast(int | None, row['favorite']) or cast(int | None, row['dismissed']) or identifier in contacts
                           or identifier in noted or identifier in compared)
            if not (cast(int, row['rankable']) or labeled):
                continue
            extracted = cast(str | None, row['extraction'])
            extraction = cast(StoredExtraction, loads(extracted)) if extracted else None
            targets.append({'id': identifier, 'fingerprint': cast(str, row['fingerprint']), 'labeled': labeled, 'first_seen': cast(str, row['first_seen']),
                            'photos': cast(Listing, loads(cast(str, row['data'])))['photos'],
                            'rooms': {photo['url']: photo['room'] for photo in extraction['photos']} if extraction else None})
        return sorted(targets, key=lambda target: (not target['labeled'], target['first_seen'], target['id']))

    def save_photo_features(self, model: str, features: Mapping[str, PhotoFeatures], similarities: Iterable[PhotoSimilarity]) -> int:
        """Replaces the photo encoder's results with those of one derivation: features by listing id, each with the
        fingerprint it was computed for, probe matches, room types and reread mark, and similarities as (room, listing,
        other listing, value). A listing whose content changed meanwhile is left out, with its similarities."""
        now = datetime.now(timezone.utc).isoformat()
        with self.connect(write=True) as connection:
            current = {cast(str, row['id']): cast(str, row['fingerprint']) for row in select(connection, 'SELECT id, fingerprint FROM listings')}
            kept = {identifier for identifier, value in features.items() if current.get(identifier) == value['fingerprint']}
            _ = connection.execute('DELETE FROM photo_features')
            _ = connection.execute('DELETE FROM photo_similarity')
            _ = connection.executemany('INSERT INTO photo_features VALUES (?, ?, ?, ?, ?, ?, ?)', [
                (identifier, value['fingerprint'], model, json.dumps(value['probes']), json.dumps(value['rooms']), int(value['reread']), now)
                for identifier, value in sorted(features.items()) if identifier in kept])
            _ = connection.executemany('INSERT INTO photo_similarity VALUES (?, ?, ?, ?, ?)', [
                (model, room, other, identifier, value) for room, identifier, other, value in similarities
                if identifier in kept and other in kept])
        return len(kept)

    def photo_rereads(self) -> set[str]:
        """The listings whose extraction disagrees with the encoder's room types on most photos and has not been read a
        second time for this content. Empty unless the encoder's room types passed their quality check."""
        with self.connect() as connection:
            encoder = self.encoder_state(connection)
            if not encoder['room_passed']:
                return set()
            return {cast(str, row['listing_id']) for row in select(connection, '''SELECT p.listing_id FROM photo_features p
                JOIN listings l ON l.id = p.listing_id AND l.fingerprint = p.fingerprint
                LEFT JOIN photo_rereads d ON d.listing_id = p.listing_id AND d.fingerprint = p.fingerprint
                WHERE p.model = ? AND p.reread = 1 AND d.listing_id IS NULL''', (encoder['model'],))}

    def begin_blind_test(self, model: str, criterion: str, listings: object) -> int:
        """Records a blind test's criterion and listings before any pick, and returns its id."""
        with self.connect(write=True) as connection:
            return inserted(connection.execute('INSERT INTO encoder_blind_tests (model, created_at, criterion, listings) VALUES (?, ?, ?, ?)',
                                               (model, datetime.now(timezone.utc).isoformat(), criterion, json.dumps(listings))))

    def finish_blind_test(self, identifier: int, picks: object, result: object) -> None:
        """Stores the owner's picks and the result of a begun blind test, once."""
        with self.connect(write=True) as connection:
            if not connection.execute('UPDATE encoder_blind_tests SET picks = ?, result = ? WHERE id = ? AND result IS NULL',
                                      (json.dumps(picks), json.dumps(result), identifier)).rowcount:
                raise ValueError(f'Blind test {identifier} does not exist or already has its picks.')

    def record_signals(self, events: object) -> int:
        """Stores an atomic, retryable batch without changing the gallery's content revision."""
        if not is_array(events) or not 1 <= len(events) <= 20:
            raise ValueError('Signals must contain 1 to 20 events.')
        fields = {'event_id', 'visit_id', 'listing_id', 'fingerprint', 'kind', 'occurred_at'}
        rows: list[SignalRow] = []
        for event in events:
            if not is_object(event) or event.get('kind') not in ('open', 'dwell', 'photo', 'close', 'impression'):
                raise ValueError('Signal kind must be open, dwell, photo, close or impression.')
            kind = event['kind']
            extra = {'duration_ms'} if kind == 'dwell' else {'photo_url'} if kind == 'photo' else {'rank'} if kind == 'impression' else set()
            if set(event) != fields | extra:
                raise ValueError('Signal fields do not match its kind.')
            for name in fields:
                _ = require_text(event[name], name, limit=200)
            # require_text accepted the six fields every signal has, so they are text.
            texts = {name: cast(str, event[name]) for name in fields}
            if not re.fullmatch(FINGERPRINT, texts['fingerprint']):
                raise ValueError('Signal fingerprint must identify listing content.')
            try:
                occurred = datetime.fromisoformat(texts['occurred_at'])
            except ValueError:
                raise ValueError('Signal occurred_at must be an ISO timestamp.') from None
            if occurred.tzinfo is None:
                raise ValueError('Signal occurred_at must include a timezone.')
            duration = event.get('duration_ms')
            photo = event.get('photo_url')
            rank = event.get('rank')
            if rank is not None and (type(rank) is not int or rank < 1):
                raise ValueError('Signal rank must be a positive integer or null.')
            if kind == 'dwell' and (type(duration) is not int or not 0 <= duration <= 86400000):
                raise ValueError('Signal duration_ms must be an integer from 0 to 86400000.')
            if kind == 'photo':
                _ = require_text(photo, 'photo_url')
                if not re.fullmatch(r'/media/[A-Za-z0-9._-]+', cast(str, photo)):
                    require_url(photo)
            # The field check leaves duration_ms only on a dwell and photo_url only on a photo, where the checks above settled them.
            rows.append((texts['event_id'], texts['visit_id'], texts['listing_id'], texts['fingerprint'],
                         texts['kind'], texts['occurred_at'], cast(int | None, duration), cast(str | None, photo), rank))
        with self.connect() as connection:
            _ = connection.execute('BEGIN IMMEDIATE')
            recorded = 0
            for row in rows:
                self.require_listing(connection, row[2])
                previous = select_one(connection, 'SELECT * FROM implicit_signals WHERE event_id = ?', (row[0],))
                if previous:
                    if cast(tuple[object, ...], tuple(previous)) != row:
                        raise ValueError('Signal event_id already records a different event.')
                    continue
                _ = connection.execute('INSERT INTO implicit_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', row)
                recorded += 1
            return recorded

    def save_rerank(self, result: RankingResult) -> int:
        """Replaces the whole ranking in one transaction with a rerank's result: each listing in result['ranked']
        gets its rank, reason, explanation and exploration mark, each in result['unranked'] stays without them, and
        every other listing loses its rank, reason and explanation. The summary is kept as the latest rerank, with
        the ids of the entries left out because their listing changed meanwhile as `stale`. The rerank's review pass
        waits from then on, with result['check'], the scores its leave-one-out check reads and the inputs its task
        describes, and a review still waiting for an earlier rerank is superseded. Returns the rerank's id."""
        with self.connect(write=True) as connection:
            current = {cast(str, row['id']): cast(str, row['fingerprint']) for row in select(connection, 'SELECT id, fingerprint FROM listings')}
            entries: list[tuple[str, str, int | None, str | None, str | None, int]] = [
                (identifier, entry['fingerprint'], entry['rank'], json.dumps(entry['reason'], ensure_ascii=False),
                 json.dumps(entry['explanation'], ensure_ascii=False, allow_nan=False), int(entry['exploration']))
                for identifier, entry in result['ranked'].items()]
            entries += [(identifier, entry['fingerprint'], None, None, None, 0) for identifier, entry in result['unranked'].items()]
            stale = sorted(entry[0] for entry in entries if current.get(entry[0]) != entry[1])
            fresh = [entry for entry in entries if entry[0] not in stale]
            _ = connection.execute('UPDATE ranking SET rank = NULL, reason = NULL, explanation = NULL, exploration = 0')
            _ = connection.executemany('''INSERT INTO ranking (listing_id, fingerprint, rank, reason, explanation, exploration) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(listing_id) DO UPDATE SET rank = excluded.rank, reason = excluded.reason, explanation = excluded.explanation,
                exploration = excluded.exploration, features = CASE WHEN ranking.fingerprint = excluded.fingerprint THEN ranking.features END,
                fingerprint = excluded.fingerprint''', fresh)
            now = datetime.now(timezone.utc).isoformat()
            rerank_id = inserted(connection.execute('INSERT INTO reranks (finished_at, result) VALUES (?, ?)',
                                                    (now, json.dumps({**result['summary'], 'stale': stale}, ensure_ascii=False, allow_nan=False))))
            _ = connection.execute("UPDATE reviews SET state = 'superseded', snapshot = NULL, updated_at = ? WHERE state IN ('waiting', 'running')", (now,))
            _ = connection.execute("INSERT INTO reviews (rerank_id, state, updated_at, snapshot) VALUES (?, 'waiting', ?, ?)",
                                   (rerank_id, now, json.dumps(result['check'], allow_nan=False)))
            return rerank_id

    def last_rerank(self) -> RerankRecord | None:
        """The latest rerank as rerank describes it, or None before the first."""
        return self.rerank(None)

    def rerank(self, rerank_id: int | None) -> RerankRecord | None:
        """The summary of a rerank, the latest when rerank_id is None, with its id, the time it finished and its review
        pass: the review's state, the times it was handed to the agent, when its state last changed, and its result once
        it has one. The states are waiting, running (handed to the agent), done, failed and superseded (a later rerank
        replaced it). None when there is no such rerank."""
        with self.connect() as connection:
            row = select_one(connection, 'SELECT r.*, v.state, v.attempts, v.updated_at, v.result AS review FROM reranks r '
                             + 'LEFT JOIN reviews v ON v.rerank_id = r.id WHERE ? IS NULL OR r.id = ? ORDER BY r.id DESC LIMIT 1',
                             (rerank_id, rerank_id))
            if not row:
                return None
            state, reviewed = cast(ReviewStatus | None, row['state']), cast(str | None, row['review'])
            review: ReviewRecord | None = {'state': state, 'attempts': cast(int, row['attempts']), 'updated_at': cast(str, row['updated_at']),
                                           'result': loads(reviewed) if reviewed else None} if state else None
            return {**cast(RerankSummary, loads(cast(str, row['result']))), 'id': cast(int, row['id']), 'finished_at': cast(str, row['finished_at']),
                    'review': review}

    def review_snapshot(self, rerank_id: int) -> RankingCheck | None:
        """What a waiting review reads as the rerank stored it, the scores of its leave-one-out check and the inputs its
        task describes, or None once the review ended."""
        with self.connect() as connection:
            row = select_one(connection, 'SELECT snapshot FROM reviews WHERE rerank_id = ?', (rerank_id,))
            stored = cast(str | None, row['snapshot']) if row else None
            return cast(RankingCheck, loads(stored)) if stored else None

    def ranked_listings(self) -> dict[str, RankedEntry]:
        """Each listing the latest rerank ranked whose content has not changed since: its rank, reason, explanation and
        exploration mark, by id."""
        with self.connect() as connection:
            return {cast(str, row['listing_id']): {'rank': cast(int, row['rank']), 'reason': cast(Reason, loads(cast(str, row['reason']))),
                                                   'explanation': cast(Explanation, loads(cast(str, row['explanation']))),
                                                   'exploration': bool(cast(int, row['exploration']))}
                    for row in select(connection, 'SELECT r.* FROM ranking r JOIN listings l ON l.id = r.listing_id AND l.fingerprint = r.fingerprint '
                                      + 'WHERE r.rank IS NOT NULL ORDER BY r.rank')}

    def hand_review(self, rerank_id: int) -> int | None:
        """Records that the review of a rerank was handed to the agent once more, and returns how many times it was, or
        None when the review no longer waits, because a later rerank replaced it or it ended meanwhile."""
        with self.connect(write=True) as connection:
            if not connection.execute("UPDATE reviews SET state = 'running', attempts = attempts + 1, updated_at = ? "
                                      + "WHERE rerank_id = ? AND state IN ('waiting', 'running')",
                                      (datetime.now(timezone.utc).isoformat(), rerank_id)).rowcount:
                return None
            return cast(int, single(connection, 'SELECT attempts FROM reviews WHERE rerank_id = ?', (rerank_id,))['attempts'])

    def fail_review(self, rerank_id: int, error: str) -> None:
        """Ends a waiting review without a saved review, with the error saying why."""
        with self.connect(write=True) as connection:
            _ = connection.execute("UPDATE reviews SET state = 'failed', snapshot = NULL, result = ?, updated_at = ? "
                                   + "WHERE rerank_id = ? AND state IN ('waiting', 'running')",
                                   (json.dumps({'error': error}, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), rerank_id))

    def save_review(self, rerank_id: int, ranks: Mapping[str, int], reasons: Mapping[str, Reason], result: object, *,
                    connection: sqlite3.Connection | None = None) -> None:
        """Applies a saved review in one transaction: each listing in ranks gets its rank and each in reasons its
        reason, and the review is done with result. A listing whose content changed since the rerank keeps no rank, as
        after any rerank. Raises ValueError when a later rerank replaced the review or it ended."""
        with self.connect(write=True) if connection is None else nullcontext(connection) as connection:
            review = select_one(connection, 'SELECT state FROM reviews WHERE rerank_id = ?', (rerank_id,))
            if not review or cast(str, review['state']) not in ('waiting', 'running'):
                raise ValueError(f'The review of rerank {rerank_id} no longer waits: a later rerank replaced it or it ended.')
            current = {cast(str, row['listing_id']) for row in select(
                connection, 'SELECT r.listing_id FROM ranking r JOIN listings l ON l.id = r.listing_id AND l.fingerprint = r.fingerprint WHERE r.rank IS NOT NULL')}
            _ = connection.executemany('UPDATE ranking SET rank = ? WHERE listing_id = ?',
                                       [(rank, identifier) for identifier, rank in ranks.items() if identifier in current])
            _ = connection.executemany('UPDATE ranking SET reason = ? WHERE listing_id = ?',
                                       [(json.dumps(reason, ensure_ascii=False), identifier) for identifier, reason in reasons.items() if identifier in current])
            _ = connection.execute("UPDATE reviews SET state = 'done', snapshot = NULL, result = ?, updated_at = ? WHERE rerank_id = ?",
                                   (json.dumps(result, ensure_ascii=False, allow_nan=False), datetime.now(timezone.utc).isoformat(), rerank_id))

    def reading_vocabulary(self, connection: sqlite3.Connection | None = None) -> dict[str, str]:
        """The features a reading may name: those of the feature record, the code features, the criteria areas and the
        places."""
        if connection is None:
            with self.connect() as connection:
                return self.reading_vocabulary(connection)
        settings = settings_of(select(connection, "SELECT * FROM settings WHERE name IN ('criteria', 'places')"))
        vocabulary = reading_vocabulary(sorted(self.known_tags(connection)), settings.get('criteria', {}).get('location', []),
                                        [place['name'] for place in settings.get('places', [])])
        return vocabulary | photo_vocabulary() if self.encoder_state(connection)['enabled'] else vocabulary

    def unread_notes(self) -> list[UnreadNote]:
        """The user's notes without a reading, oldest first."""
        with self.connect() as connection:
            return [cast(UnreadNote, dict(row)) for row in select(connection, '''SELECT n.id, n.listing_id, n.text, n.created_at
                FROM notes n LEFT JOIN note_readings r ON r.note_id = n.id
                WHERE n.role = 'user' AND r.note_id IS NULL ORDER BY n.id''')]

    def unread_preferences(self) -> list[tuple[Direction, str]]:
        """The stated likes and dislikes without a reading of their current wording, as (kind, phrase); while the photo
        encoder is on, those read before its probes were in the vocabulary; and while it is off, those whose reading
        names a photo feature, so the part the photo feature carried is read again into record features or a judgment."""
        with self.connect() as connection:
            settings = settings_of(select(connection, 'SELECT * FROM settings'))
            encoder = self.encoder_state(connection)
            read = {(cast(Direction, row['kind']), cast(str, row['phrase']))
                    for row in select(connection, 'SELECT kind, phrase, features, photo_model FROM preference_readings')
                    if (cast(str | None, row['photo_model']) == encoder['model'] if encoder['enabled']
                        else not any(item['feature'].startswith('photo:') for item in cast(list[FeatureReading], loads(cast(str, row['features'])))))}
        return [item for item in stated_phrases(settings) if item not in read]

    def preference_readings(self) -> dict[tuple[Direction, str], list[FeatureReading]]:
        with self.connect() as connection:
            readings = {(cast(Direction, row['kind']), cast(str, row['phrase'])): cast(list[FeatureReading], loads(cast(str, row['features'])))
                        for row in select(connection, 'SELECT * FROM preference_readings')}
            return readings if self.encoder_state(connection)['enabled'] else without_photo_features(readings)

    def note_readings(self) -> dict[int, NoteReading]:
        with self.connect() as connection:
            return {cast(int, row['note_id']): cast(NoteReading, loads(cast(str, row['reading'])))
                    for row in select(connection, 'SELECT * FROM note_readings')}

    def save_readings(self, *, notes: Iterable[tuple[int, object]] = (), preferences: Iterable[tuple[str, str, object]] = ()) -> None:
        """Stores the model's readings, all in one transaction or none: notes as (note id, {sentiment, likes,
        dislikes}) for notes by the user, preferences as (kind, phrase, features) for the stated likes and dislikes,
        each feature {feature, direction}. Features must be in the reading vocabulary."""
        with self.connect(write=True) as connection:
            vocabulary = self.reading_vocabulary(connection)
            encoder = self.encoder_state(connection)
            photo_model = encoder['model'] if encoder['enabled'] else None
            settings = settings_of(select(connection, 'SELECT * FROM settings'))
            stated = set(stated_phrases(settings))
            now = datetime.now(timezone.utc).isoformat()
            rows: list[tuple[str, str, str, str, str | None]] = []
            for kind, phrase, features in preferences:
                if (kind, phrase) not in stated:
                    raise ValueError(f'"{phrase}" is not a stated {kind}.')
                try:
                    rows.append((kind, phrase, json.dumps(validate_preference_reading(features, vocabulary), ensure_ascii=False), now, photo_model))
                except ValueError as error:
                    raise ValueError(f'The reading of "{phrase}": {error}') from None
            readings: list[tuple[int, str, str]] = []
            for note_id, reading in notes:
                note = select_one(connection, 'SELECT role FROM notes WHERE id = ?', (note_id,))
                if not note or cast(str, note['role']) != 'user':
                    raise ValueError(f'Note {note_id} is not a note by the user.')
                try:
                    readings.append((note_id, json.dumps(validate_note_reading(reading, vocabulary), ensure_ascii=False), now))
                except ValueError as error:
                    raise ValueError(f'The reading of note {note_id}: {error}') from None
            _ = connection.executemany('''INSERT INTO preference_readings VALUES (?, ?, ?, ?, ?) ON CONFLICT(kind, phrase)
                DO UPDATE SET features = excluded.features, read_at = excluded.read_at, photo_model = excluded.photo_model''', rows)
            for note_id, encoded, read_at in readings:
                previous = select_one(connection, 'SELECT reading FROM note_readings WHERE note_id = ?', (note_id,))
                current = cast(NoteReading, loads(encoded))
                earlier = cast(NoteReading, loads(cast(str, previous['reading']))) if previous else None
                if current != earlier and (current['sentiment'] in ('positive', 'negative') or earlier and earlier['sentiment'] in ('positive', 'negative')):
                    identifier = cast(str, single(connection, 'SELECT listing_id FROM notes WHERE id = ?', (note_id,))[0])
                    self.record_learning_event(connection, 'note', identifier, current['sentiment'])
                _ = connection.execute('''INSERT INTO note_readings VALUES (?, ?, ?) ON CONFLICT(note_id)
                    DO UPDATE SET reading = excluded.reading, read_at = excluded.read_at''', (note_id, encoded, read_at))

    def answer_pair(self, chosen: str, other: str) -> None:
        """Records the user's answer to a pairwise question: chosen over other."""
        with self.connect(write=True) as connection:
            self.require_pair(connection, chosen, other)
            previous = select_one(connection, '''SELECT chosen FROM pairwise_answers
                WHERE (chosen = ? AND other = ?) OR (chosen = ? AND other = ?)''', (chosen, other, other, chosen))
            if previous:
                if cast(str, previous['chosen']) != chosen:
                    raise ValueError('This pair was already answered.')
                return
            self.record_learning_event(connection, 'pair', chosen, other)
            _ = connection.execute('INSERT INTO pairwise_answers (chosen, other, answered_at) VALUES (?, ?, ?)',
                                   (chosen, other, datetime.now(timezone.utc).isoformat()))
            self.advance_pairwise_skips(connection)

    def require_pair(self, connection: sqlite3.Connection, first: str, second: str) -> None:
        _ = require_text(first, 'First listing')
        _ = require_text(second, 'Second listing')
        if first == second:
            raise ValueError('A pairwise question compares two different listings.')
        for identifier in (first, second):
            self.require_listing(connection, identifier)
            available = select_one(connection, f'''SELECT 1 FROM listings l LEFT JOIN feedback f ON f.listing_id = l.id
                WHERE l.id = ? AND {RANKABLE}''', (identifier,))
            if not available:
                raise ValueError('This listing is no longer available to compare. Refresh Compare.')

    @staticmethod
    def advance_pairwise_skips(connection: sqlite3.Connection) -> None:
        _ = connection.execute('DELETE FROM pairwise_skips WHERE remaining = 1')
        _ = connection.execute('UPDATE pairwise_skips SET remaining = remaining - 1')

    def compare_invitation(self) -> CompareInvitation:
        """The invitation to answer the first pairwise questions, one record every surface reads: whether it is due,
        because the interview is complete, at least two listings the ranking can compare have a feature record of their
        current content and the user has given no pairwise answer yet; when each surface delivered it, by surface:
        gateway (the messaging gateway), gallery (shown in the gallery while it is due) and conversation (asked in a
        conversation with the agent); and the last attempt that failed, {'at', 'reason'}, while no surface has
        delivered it since, otherwise None. Sample listings do not count."""
        missing = self.missing_settings()
        with self.connect() as connection:
            _ = connection.execute('BEGIN')
            comparable = cast(int, single(connection, f'''SELECT COUNT(*) FROM listings l LEFT JOIN feedback f ON f.listing_id = l.id
                JOIN feature_records records ON records.listing_id = l.id AND records.fingerprint = l.fingerprint
                WHERE {RANKABLE} AND l.id NOT LIKE 'sample:%' ''')[0])
            answered = select_one(connection, 'SELECT 1 FROM pairwise_answers LIMIT 1')
            row = single(connection, 'SELECT * FROM compare_invitation')
        failed_at = cast(str | None, row['failed_at'])
        return {'due': not missing and comparable >= 2 and not answered,
                'delivered': {surface: delivered for surface in COMPARE_SURFACES if (delivered := cast(str | None, row[surface]))},
                'failed': {'at': failed_at, 'reason': cast(str, row['failure'])} if failed_at else None}

    def deliver_compare_invitation(self, surface: str) -> bool:
        """Records that surface delivered the invitation to answer the first pairwise questions, which ends any failed
        attempt, and returns whether this is the first time it did."""
        if surface not in COMPARE_SURFACES:
            raise ValueError(f'{surface} is not a surface of the Compare invitation.')
        with self.connect(write=True) as connection:
            first = cast(int, single(connection, f'SELECT {surface} IS NULL FROM compare_invitation')[0])
            _ = connection.execute(f'UPDATE compare_invitation SET {surface} = COALESCE({surface}, ?), failed_at = NULL, failure = NULL',
                                   (datetime.now(timezone.utc).isoformat(),))
            return bool(first)

    def fail_compare_invitation(self, reason: str) -> None:
        """Records an attempt to deliver the invitation that failed, and why, unless a surface delivered it already."""
        with self.connect(write=True) as connection:
            _ = connection.execute('UPDATE compare_invitation SET failed_at = ?, failure = ? '
                                   + 'WHERE gateway IS NULL AND gallery IS NULL AND conversation IS NULL',
                                   (datetime.now(timezone.utc).isoformat(), reason))

    def skip_pair(self, first: str, second: str) -> None:
        """Suppresses this pair until twenty other questions have been answered or skipped."""
        with self.connect(write=True) as connection:
            self.require_pair(connection, first, second)
            first, second = sorted((first, second))
            if select_one(connection, 'SELECT 1 FROM pairwise_skips WHERE first = ? AND second = ?', (first, second)):
                return
            if select_one(connection, '''SELECT 1 FROM pairwise_answers
                WHERE (chosen = ? AND other = ?) OR (chosen = ? AND other = ?)''', (first, second, second, first)):
                raise ValueError('This pair was already answered.')
            self.advance_pairwise_skips(connection)
            _ = connection.execute('INSERT INTO pairwise_skips VALUES (?, ?, 20)', (first, second))

    def skipped_pairs(self) -> list[tuple[str, str]]:
        with self.connect() as connection:
            return [(cast(str, row['first']), cast(str, row['second'])) for row in select(connection, 'SELECT first, second FROM pairwise_skips')]

    def pairwise_answers(self) -> list[tuple[str, str]]:
        with self.connect() as connection:
            return answered_pairs(connection)

    def given_up_extractions(self) -> set[str]:
        """The listings whose current content failed extraction READ_ATTEMPTS times and has no record. The ranking ranks
        them from their code features."""
        return {listing['id'] for listing in self.ranking_inputs()['listings'] if listing['given_up']}

    def rank(self, listing_id: str, fingerprint: str, rank: object, reason_features: object) -> None:
        """Sets one listing's rank and a reason whose features part is reason_features, either {'for': [...], 'against':
        [...]} or {'text': ...}."""
        if type(rank) is not int or rank < 1:
            raise ValueError('Rank must be a positive integer.')
        if not is_object(reason_features) or set(reason_features) not in ({'for', 'against'}, {'text'}):
            raise ValueError('A reason names its features as for and against lists or as a text.')
        reason = compose_reason(cast(FeatureReason | ReasonText, reason_features), code_only=False, similar_favorite=None)
        with self.connect(write=True) as connection:
            self.require_fingerprint(connection, listing_id, fingerprint)
            _ = connection.execute('''INSERT INTO ranking (listing_id, fingerprint, rank, reason) VALUES (?, ?, ?, ?)
                ON CONFLICT(listing_id) DO UPDATE SET rank = excluded.rank, reason = excluded.reason,
                features = CASE WHEN ranking.fingerprint = excluded.fingerprint THEN ranking.features END,
                fingerprint = excluded.fingerprint''', (listing_id, fingerprint, rank, json.dumps(reason, ensure_ascii=False)))

    def save_extraction(self, listing_id: str, fingerprint: str, extraction: object, *, photos: Sequence[str], phrases: Iterable[str]) -> SavedExtraction:
        """Stores the model's part of the feature record of this content when it keeps the contract of record.py.
        photos are the unique photo URLs the extraction numbered from 1, phrases the stated preferences it judged.
        A trait spelled like a known tag but for hyphens and spaces takes the known spelling. New traits and concerns
        join the tag list in the same transaction. Returns the new tags."""
        validated = validate_extraction(extraction, len(photos), phrases)
        preferences = validated['preferences']
        record: StoredExtraction = {
            'photos': [{**photo, 'url': photos[photo['index'] - 1]} for photo in validated['photos']],
            'ratings': validated['ratings'], 'floor': validated['floor'], 'style': validated['style'],
            'outdoor_space': validated['outdoor_space'], 'building_type': validated['building_type'],
            'unit_level': validated['unit_level'], 'flags': validated['flags'],
            'traits': validated['traits'], 'concerns': validated['concerns'], 'summary': validated['summary'],
        }
        with self.connect(write=True) as connection:
            self.require_fingerprint(connection, listing_id, fingerprint)
            known = self.known_tags(connection)
            record['traits'] = list(dict.fromkeys(known_spelling(tag, known) for tag in record['traits']))
            record['concerns'] = list(dict.fromkeys(known_spelling(tag, known) for tag in record['concerns']))
            new_tags = sorted({*record['traits'], *record['concerns']} - known)
            now = datetime.now(timezone.utc).isoformat()
            _ = connection.executemany('INSERT INTO tags VALUES (?, ?)', [(tag, now) for tag in new_tags])
            _ = connection.execute('''INSERT INTO feature_records VALUES (?, ?, ?, ?, ?) ON CONFLICT(listing_id) DO UPDATE SET
                fingerprint = excluded.fingerprint, extraction = excluded.extraction, extracted_at = excluded.extracted_at,
                preferences = CASE WHEN feature_records.fingerprint = excluded.fingerprint
                              THEN json_patch(feature_records.preferences, excluded.preferences) ELSE excluded.preferences END''',
                                   (listing_id, fingerprint, json.dumps(record, ensure_ascii=False), now, json.dumps(preferences, ensure_ascii=False)))
            _ = connection.execute('''INSERT OR IGNORE INTO photo_rereads SELECT listing_id, fingerprint FROM photo_features
                WHERE listing_id = ? AND fingerprint = ? AND reread = 1''', (listing_id, fingerprint))
            return {'new_tags': new_tags}

    def judge_preferences(self, entries: Iterable[tuple[str, str, object, Iterable[str]]]) -> None:
        """Adds judgments of stated preferences made after the extraction of a listing's content, from its record and text.
        entries are (listing id, fingerprint, judgments, phrases judged). All are stored in one transaction, or none when
        any is invalid or its listing has no record of that content."""
        validated = [(listing_id, fingerprint, validate_judgments(judgments, phrases)) for listing_id, fingerprint, judgments, phrases in entries]
        with self.connect(write=True) as connection:
            for listing_id, fingerprint, judgments in validated:
                self.require_fingerprint(connection, listing_id, fingerprint)
                if not connection.execute('UPDATE feature_records SET preferences = json_patch(preferences, ?) WHERE listing_id = ? AND fingerprint = ?',
                                          (json.dumps(judgments, ensure_ascii=False), listing_id, fingerprint)).rowcount:
                    raise ValueError(f'{listing_id} has no feature record of its current content to judge preferences from.')

    def feature_records(self) -> dict[str, FeatureRecord]:
        """The feature record of every listing whose current content has one: its extraction, the preferences judged for
        it and when it was extracted."""
        return self.current_feature_records()

    def feature_record(self, listing_id: str) -> FeatureRecord | None:
        return self.current_feature_records(listing_id).get(listing_id)

    def current_feature_records(self, listing_id: str | None = None) -> dict[str, FeatureRecord]:
        with self.connect() as connection:
            return {cast(str, row['listing_id']): {'extraction': cast(StoredExtraction, loads(cast(str, row['extraction']))),
                                                   'preferences': cast(dict[str, Judgment], loads(cast(str, row['preferences']))),
                                                   'extracted_at': cast(str, row['extracted_at'])}
                    for row in select(connection, '''SELECT r.* FROM feature_records r JOIN listings l ON l.id = r.listing_id
                        AND l.fingerprint = r.fingerprint WHERE ? IS NULL OR r.listing_id = ?
                        ORDER BY r.listing_id''', (listing_id, listing_id))}

    def tags(self) -> list[str]:
        """The tag list: the seed tags of record.py and every tag an extraction added."""
        with self.connect() as connection:
            return sorted(self.known_tags(connection))

    @staticmethod
    def known_tags(connection: sqlite3.Connection) -> set[str]:
        return {*SEED_TAGS, *(cast(str, row['tag']) for row in select(connection, 'SELECT tag FROM tags'))}

    def read_attempts(self) -> dict[str, int]:
        """How many times each model read was handed to the agent without its result being saved."""
        with self.connect() as connection:
            return {cast(str, row['read']): cast(int, row['attempts']) for row in select(connection, 'SELECT * FROM model_reads')}

    def record_read_attempts(self, failed: Iterable[str], waiting: Iterable[str]) -> None:
        """Counts one more failed attempt for each read in failed and forgets every read not in waiting or failed."""
        with self.connect(write=True) as connection:
            keep = {*waiting, *failed}
            _ = connection.executemany('DELETE FROM model_reads WHERE read = ?',
                                       [(read,) for row in select(connection, 'SELECT read FROM model_reads') if (read := cast(str, row[0])) not in keep])
            _ = connection.executemany('INSERT INTO model_reads VALUES (?, 1) ON CONFLICT(read) DO UPDATE SET attempts = attempts + 1',
                                       [(read,) for read in failed])

    def reset_read_attempts(self) -> None:
        with self.connect(write=True) as connection:
            _ = connection.execute('DELETE FROM model_reads')

    @staticmethod
    def require_fingerprint(connection: sqlite3.Connection, listing_id: str, fingerprint: str) -> None:
        ListingStore.require_listing(connection, listing_id)
        if cast(str, single(connection, 'SELECT fingerprint FROM listings WHERE id = ?', (listing_id,))[0]) != fingerprint:
            raise ValueError('Listing content changed. Read the current listing before saving analysis or rank.')

    def listing(self, listing_id: str, *, notes_page: int = 1, correspondence_page: int = 1, viewings_page: int = 1,
                visible_only: bool = False) -> ListingDetail:
        with self.connect() as connection:
            _ = connection.execute('BEGIN')
            row = select_one(connection, 'SELECT * FROM listings WHERE id = ?', (listing_id,))
            if not row or visible_only and not cast(int, row['matches_criteria']) and not select_one(
                    connection, 'SELECT 1 FROM contacts WHERE listing_id = ? UNION SELECT 1 FROM viewings WHERE listing_id = ?', (listing_id, listing_id)):
                raise KeyError('Listing not found.')
            data = cast(Listing, loads(cast(str, row['data'])))
            fingerprint = cast(str, row['fingerprint'])
            ranking = select_one(connection, 'SELECT * FROM ranking WHERE listing_id = ?', (listing_id,))
            reason = cast(str | None, ranking['reason']) if ranking else None
            explanation = cast(str | None, ranking['explanation']) if ranking else None
            feedback = select_one(connection, 'SELECT * FROM feedback WHERE listing_id = ?', (listing_id,))
            contact = select_one(connection, 'SELECT * FROM contacts WHERE listing_id = ?', (listing_id,))

            def page_of(table: str, page: int) -> tuple[int, list[sqlite3.Row]]:
                validate_page(page, 50)
                total = cast(int, single(connection, f'SELECT COUNT(*) FROM {table} WHERE listing_id = ?', (listing_id,))[0])
                order = 'time, id' if table == 'viewings' else 'id DESC'
                rows = list(select(connection, f'SELECT * FROM {table} WHERE listing_id = ? ORDER BY {order} LIMIT 50 OFFSET ?',
                                   (listing_id, (page - 1) * 50)))
                return total, rows if table == 'viewings' else list(reversed(rows))

            notes_total, notes = page_of('notes', notes_page)
            correspondence_total, correspondence = page_of('correspondence', correspondence_page)
            viewings_total, viewings = page_of('viewings', viewings_page)
            detail: ListingDetail = {
                **data, 'matches_criteria': bool(cast(int, row['matches_criteria'])),
                'rank': cast(int | None, ranking['rank']) if ranking else None, 'reason': cast(Reason, loads(reason)) if reason else None,
                'explanation': cast(Explanation, loads(explanation)) if explanation else None,
                'exploration': bool(ranking and cast(int, ranking['exploration'])),
                'content_fingerprint': fingerprint, 'analysis_current': bool(ranking and cast(str, ranking['fingerprint']) == fingerprint),
                'favorite': bool(feedback and cast(int, feedback['favorite'])), 'dismissed': bool(feedback and cast(int, feedback['dismissed'])),
                'contact': contact_record(contact) if contact else None,
                'notes_total': notes_total, 'notes': [cast(NoteRecord, dict(note)) for note in notes],
                'correspondence_total': correspondence_total, 'correspondence': [cast(CorrespondenceRecord, dict(message)) for message in correspondence],
                'viewings_total': viewings_total, 'viewings': [cast(ViewingRecord, dict(viewing)) for viewing in viewings],
            }
            explain_pending_rankings(connection, [detail])
            if detail['rank'] is None:
                detail['explanation'] = None
            return detail

    def state(self) -> Snapshot:
        with self.connect() as connection:
            return snapshot(connection)

    def revision(self) -> int:
        with self.connect() as connection:
            return cast(int, single(connection, 'SELECT revision FROM changes WHERE id = 1')[0])

    def start_chat(self, text: object, request_id: object) -> int:
        message = require_text(text, 'Message')
        request = require_text(request_id, 'Request id', limit=100)
        with self.connect() as connection:
            _ = connection.execute('BEGIN IMMEDIATE')
            if select_one(connection, "SELECT 1 FROM chat_messages WHERE status = 'running'"):
                raise ValueError('A reply is already in progress.')
            if select_one(connection, 'SELECT 1 FROM chat_messages WHERE request_id = ?', (request,)):
                raise ValueError('This message was already submitted. Refresh the conversation.')
            now = datetime.now(timezone.utc).isoformat()
            _ = connection.execute("INSERT INTO chat_messages (role, content, status, created_at, request_id) VALUES ('user', ?, 'complete', ?, ?)",
                                   (message, now, request))
            return inserted(connection.execute("INSERT INTO chat_messages (role, content, status, created_at) VALUES ('agent', '', 'running', ?)", (now,)))

    def update_chat(self, message_id: int, *, content: str | None = None, status: MessageStatus | None = None, events: list[ToolEvent] | None = None,
                    prompt: ChatPrompt | None | Unchanged = UNCHANGED, error: str | None | Unchanged = UNCHANGED, session_id: str | None = None,
                    admission_id: str | None = None) -> None:
        with self.connect() as connection:
            _ = connection.execute('BEGIN IMMEDIATE')
            if status is not None and status not in ('running', 'complete', 'error'):
                raise ValueError('Unknown message status.')
            _ = connection.execute('''UPDATE chat_messages SET content = COALESCE(?, content), status = COALESCE(?, status),
                events = COALESCE(?, events), prompt = CASE WHEN ? THEN ? ELSE prompt END,
                error = CASE WHEN ? THEN ? ELSE error END, admission_id = COALESCE(?, admission_id) WHERE id = ?''',
                (content, status, json.dumps(events) if events is not None else None,
                 prompt is not UNCHANGED, json.dumps(prompt) if prompt is not UNCHANGED and prompt is not None else None,
                 error is not UNCHANGED, error if error is not UNCHANGED else None, admission_id, message_id))
            if session_id:
                _ = connection.execute('UPDATE notes_session SET session_id = ? WHERE id = 1', (session_id,))

    def chat_state(self, page: int = 1) -> ChatState:
        validate_page(page, 20)
        with self.connect() as connection:
            rows = select(connection, 'SELECT * FROM chat_messages ORDER BY id DESC LIMIT 20 OFFSET ?', ((page - 1) * 20,))
            messages = [cast(ChatMessage, {**dict(row), 'events': loads(cast(str, row['events'])),
                                           'prompt': loads(prompt) if (prompt := cast(str | None, row['prompt'])) else None}) for row in rows]
            return {'messages': list(reversed(messages)), 'total': cast(int, single(connection, 'SELECT COUNT(*) FROM chat_messages')[0]),
                    'active': bool(select_one(connection, "SELECT 1 FROM chat_messages WHERE status = 'running'")),
                    'session_id': cast(str | None, single(connection, 'SELECT session_id FROM notes_session WHERE id = 1')[0])}

    def browse(self, view: str = 'Rankings', *, page: int = 1, page_size: int = 24) -> Page:
        with self.connect() as connection:
            result = browse(connection, view, page, page_size)
            explain_pending_rankings(connection, result['items'])
            return result

    def feedback(self, listing_id: str, *, favorite: object = None, dismissed: object = None) -> None:
        for value in (favorite, dismissed):
            if value is not None and type(value) is not bool:
                raise ValueError('Feedback must be true or false.')
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            _ = connection.execute('INSERT OR IGNORE INTO feedback (listing_id) VALUES (?)', (listing_id,))
            previous = single(connection, 'SELECT * FROM feedback WHERE listing_id = ?', (listing_id,))
            was_dismissed, was_favorite = bool(cast(int, previous['dismissed'])), bool(cast(int, previous['favorite']))
            if dismissed is not None and dismissed != was_dismissed:
                self.record_learning_event(connection, 'dismiss' if dismissed else 'restore', listing_id)
                _ = connection.execute('UPDATE feedback SET dismissed = ?, favorite = CASE WHEN ? THEN 0 ELSE favorite END WHERE listing_id = ?',
                                       (dismissed, dismissed, listing_id))
            is_dismissed = was_dismissed if dismissed is None else dismissed
            is_favorite = was_favorite and not is_dismissed
            if favorite is not None and favorite != is_favorite and not is_dismissed:
                self.record_learning_event(connection, 'favorite' if favorite else 'unfavorite', listing_id)
                _ = connection.execute('UPDATE feedback SET favorite = ? WHERE listing_id = ?', (favorite, listing_id))

    def add_note(self, text: object, *, listing_id: str, role: str = 'user') -> None:
        note = require_text(text, 'Note')
        if role not in ('user', 'agent'):
            raise ValueError('Note role must be user or agent.')
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            _ = connection.execute('INSERT INTO notes (listing_id, role, text, created_at) VALUES (?, ?, ?, ?)',
                                   (listing_id, role, note, datetime.now(timezone.utc).isoformat()))

    def request_contact(self, listing_id: str) -> None:
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            if not select_one(connection, 'SELECT 1 FROM contacts WHERE listing_id = ?', (listing_id,)):
                self.record_learning_event(connection, 'contact', listing_id)
            agent = cast(str, single(connection, "SELECT json_extract(data, '$.agent') FROM listings WHERE id = ?", (listing_id,))[0])
            _ = connection.execute('INSERT OR IGNORE INTO contacts (listing_id, status, requested_at, agent) VALUES (?, ?, ?, ?)',
                                   (listing_id, 'pending', datetime.now(timezone.utc).isoformat(), agent))
            _ = connection.execute("UPDATE contacts SET status = 'pending', error = NULL WHERE listing_id = ? AND status = 'failed'", (listing_id,))

    def record_learning_event(self, connection: sqlite3.Connection, kind: LearningKind, listing: str, other: str | None = None) -> None:
        occurred_at = datetime.now(timezone.utc).isoformat()
        inputs = self.ranking_inputs(connection=connection)
        if kind == 'favorite':
            inputs['impressions'] = self.favorite_impressions(connection, listing, occurred_at)
        _ = connection.execute('INSERT INTO learning_events (occurred_at, kind, listing, other, inputs) VALUES (?, ?, ?, ?, ?)',
                               (occurred_at, kind, listing, other, encode(inputs)))

    def favorite_impressions(self, connection: sqlite3.Connection, listing: str, occurred_at: str) -> list[Impression]:
        visit = select_one(connection, '''SELECT s.visit_id FROM implicit_signals s JOIN listings l
            ON l.id = s.listing_id AND l.fingerprint = s.fingerprint
            WHERE s.kind = 'impression' AND s.listing_id = ? AND s.shown_rank IS NOT NULL
            AND julianday(s.occurred_at) <= julianday(?)
            ORDER BY julianday(s.occurred_at) DESC, s.rowid DESC LIMIT 1''', (listing, occurred_at))
        if visit is None:
            return []
        return [cast(Impression, dict(row)) for row in select(connection, '''SELECT listing_id, fingerprint, visit_id, shown_rank, occurred_at
            FROM (SELECT s.*, row_number() OVER (PARTITION BY s.listing_id ORDER BY julianday(s.occurred_at) DESC, s.rowid DESC) AS latest
                FROM implicit_signals s JOIN listings l ON l.id = s.listing_id AND l.fingerprint = s.fingerprint
                WHERE s.kind = 'impression' AND s.visit_id = ? AND s.shown_rank IS NOT NULL
                AND julianday(s.occurred_at) <= julianday(?))
            WHERE latest = 1 ORDER BY listing_id''', (cast(str, visit['visit_id']), occurred_at))]

    def correspond(self, listing_id: str, direction: str, channel: str, text: object, *, external_message_id: object,
                   external_thread_id: object = None) -> bool:
        message = require_text(text, 'Message')
        if channel not in CONTACT_CHANNELS:
            raise ValueError('Unknown contact channel.')
        _ = require_text(external_message_id, 'External message id', limit=1000)
        if external_thread_id is not None:
            _ = require_text(external_thread_id, 'External thread id', limit=1000)
        if direction not in ('incoming', 'outgoing'):
            raise ValueError('Direction must be incoming or outgoing.')
        with self.connect(write=True) as connection:
            if not select_one(connection, 'SELECT 1 FROM contacts WHERE listing_id = ?', (listing_id,)):
                raise KeyError('Contact request not found.')
            now = datetime.now(timezone.utc).isoformat()
            cursor = connection.execute('''INSERT INTO correspondence
                (listing_id, direction, channel, text, created_at, external_message_id, external_thread_id) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(listing_id, channel, external_message_id) DO NOTHING''',
                (listing_id, direction, channel, message, now, external_message_id, external_thread_id))
            if cursor.rowcount and direction == 'outgoing':
                _ = connection.execute('UPDATE contacts SET handled_through = ? WHERE listing_id = ?', (cursor.lastrowid, listing_id))
                self.handle_answers(connection, listing_id)
            return bool(cursor.rowcount)

    @staticmethod
    def handle_answers(connection: sqlite3.Connection, listing_id: str) -> None:
        _ = connection.execute('UPDATE questions SET handled = 1 WHERE listing_id = ? AND answered_at IS NOT NULL', (listing_id,))

    def has_message(self, listing_id: str, channel: str, external_message_id: str) -> bool:
        with self.connect() as connection:
            return select_one(connection, 'SELECT 1 FROM correspondence WHERE listing_id = ? AND channel = ? AND external_message_id = ?',
                              (listing_id, channel, external_message_id)) is not None

    def mark_handled(self, listing_id: str) -> None:
        with self.connect(write=True) as connection:
            if not connection.execute('''UPDATE contacts SET handled_through = (SELECT COALESCE(MAX(id), 0) FROM correspondence
                WHERE listing_id = ?) WHERE listing_id = ?''', (listing_id, listing_id)).rowcount:
                raise KeyError('Contact request not found.')
            self.handle_answers(connection, listing_id)

    def due_contacts(self) -> list[DueContact]:
        with self.connect() as connection:
            return [{'listing_id': cast(str, row['listing_id']),
                     'reason': 'reply' if cast(int, row['reply']) else 'answer' if cast(int, row['answer']) else 'start'}
                    for row in select(connection, '''SELECT * FROM (SELECT c.listing_id, c.status, c.requested_at,
                    EXISTS (SELECT 1 FROM correspondence m WHERE m.listing_id = c.listing_id AND m.direction = 'incoming'
                            AND m.id > c.handled_through) AS reply,
                    EXISTS (SELECT 1 FROM questions q WHERE q.listing_id = c.listing_id AND q.answered_at IS NOT NULL AND q.handled = 0) AS answer,
                    NOT EXISTS (SELECT 1 FROM correspondence m WHERE m.listing_id = c.listing_id AND m.direction = 'outgoing') AS unsent,
                    EXISTS (SELECT 1 FROM questions q WHERE q.listing_id = c.listing_id AND q.answered_at IS NULL) AS waiting
                FROM contacts c WHERE c.status IN ('pending', 'in_progress', 'contacted'))
                WHERE NOT waiting AND (reply OR answer OR (unsent AND status IN ('pending', 'in_progress')))
                ORDER BY requested_at, listing_id''')]

    def thread_owners(self, channel: str) -> dict[str, str]:
        """Maps each thread of an open contact to the listing that last wrote in it, so a shared agency address or
        number files a reply under the conversation it continues."""
        with self.connect() as connection:
            return {cast(str, row['external_thread_id']): cast(str, row['listing_id']) for row in select(connection, '''SELECT m.listing_id, m.external_thread_id,
                MAX(m.id) AS last FROM correspondence m JOIN contacts c ON c.listing_id = m.listing_id
                WHERE m.channel = ? AND m.direction = 'outgoing' AND m.external_thread_id IS NOT NULL
                AND c.status IN ('pending', 'in_progress', 'contacted') GROUP BY m.listing_id, m.external_thread_id ORDER BY last''', (channel,))}

    def active_contacts(self) -> list[ContactRecord]:
        with self.connect() as connection:
            return [contact_record(row) for row in select(
                connection, "SELECT * FROM contacts WHERE status IN ('pending', 'in_progress', 'contacted') ORDER BY listing_id")]

    def ask_user(self, listing_id: str, question: object, *, field: object = None, purpose: object = None, external_message_id: str | None = None) -> int:
        asked = require_text(question, 'Question', limit=4000)
        field_name = None if field is None else require_text(field, 'Field', limit=200)
        purpose_text = None if purpose is None else require_text(purpose, 'Purpose', limit=1000)
        with self.connect(write=True) as connection:
            if not select_one(connection, 'SELECT 1 FROM contacts WHERE listing_id = ?', (listing_id,)):
                raise KeyError('Contact request not found.')
            _ = connection.execute("UPDATE contacts SET status = 'in_progress' WHERE listing_id = ? AND status = 'pending'", (listing_id,))
            return inserted(connection.execute('''INSERT INTO questions (listing_id, question, field, purpose, asked_at, external_message_id)
                VALUES (?, ?, ?, ?, ?, ?)''', (listing_id, asked, field_name, purpose_text, datetime.now(timezone.utc).isoformat(),
                                               external_message_id)))

    def unhandled_answers(self, listing_id: str) -> list[QuestionRecord]:
        with self.connect() as connection:
            return [cast(QuestionRecord, dict(row)) for row in select(
                connection, 'SELECT * FROM questions WHERE listing_id = ? AND answered_at IS NOT NULL AND handled = 0 ORDER BY id', (listing_id,))]

    def open_questions(self, listing_id: str | None = None) -> list[QuestionRecord]:
        with self.connect() as connection:
            return [cast(QuestionRecord, dict(row)) for row in select(
                connection, 'SELECT * FROM questions WHERE answered_at IS NULL AND (? IS NULL OR listing_id = ?) ORDER BY id', (listing_id, listing_id))]

    def answer_question(self, question_id: int, answer: object, *, share: object = None) -> None:
        answered = require_text(answer, 'Answer')
        shared = None if share is None else require_text(share, 'Shared value')
        with self.connect(write=True) as connection:
            question = select_one(connection, 'SELECT * FROM questions WHERE id = ?', (question_id,))
            if not question:
                raise KeyError('Question not found.')
            if cast(str | None, question['answered_at']):
                raise ValueError('This question is already answered.')
            field, purpose = cast(str | None, question['field']), cast(str | None, question['purpose'])
            if shared is not None and not (field and purpose):
                raise ValueError('Only a question that names a field and purpose can approve sharing a value.')
            now = datetime.now(timezone.utc).isoformat()
            _ = connection.execute('UPDATE questions SET answer = ?, answered_at = ? WHERE id = ?', (answered, now, question_id))
            if shared is not None:
                _ = connection.execute('INSERT INTO approvals (listing_id, field, value, purpose, approved_at) VALUES (?, ?, ?, ?, ?)',
                                       (cast(str, question['listing_id']), field, shared, purpose, now))

    def set_contact(self, listing_id: str, status: str, *, channel: str | None = None, agent: object = None, error: object = None,
                    subject: object = None) -> None:
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
        if subject is not None:
            subject = require_text(subject, 'Subject', limit=500)
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            current = select_one(connection, 'SELECT agent FROM contacts WHERE listing_id = ?', (listing_id,))
            if not current:
                raise KeyError('Contact request not found.')
            _ = connection.execute('''UPDATE contacts SET status = ?, channel = COALESCE(?, channel), agent = ?, error = ?,
                subject = COALESCE(?, subject) WHERE listing_id = ?''',
                                   (status, channel, json.dumps(agent) if agent is not None else cast(str, current['agent']), error, subject, listing_id))

    def approve_information(self, listing_id: str, field: object, value: object, purpose: object) -> int:
        field_name = require_text(field, 'Field', limit=200)
        purpose_text = require_text(purpose, 'Purpose', limit=1000)
        approved = require_text(value, 'Approved value')
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            return inserted(connection.execute('INSERT INTO approvals (listing_id, field, value, purpose, approved_at) VALUES (?, ?, ?, ?, ?)',
                                               (listing_id, field_name, approved, purpose_text, datetime.now(timezone.utc).isoformat())))

    def approved_information(self, listing_id: str, purpose: str) -> list[ApprovalRecord]:
        with self.connect() as connection:
            return [cast(ApprovalRecord, dict(row)) for row in select(
                connection, 'SELECT * FROM approvals WHERE listing_id = ? AND purpose = ? AND revoked_at IS NULL ORDER BY id', (listing_id, purpose))]

    def approvals(self, listing_id: str) -> list[ApprovalRecord]:
        with self.connect() as connection:
            return [cast(ApprovalRecord, dict(row)) for row in select(
                connection, 'SELECT * FROM approvals WHERE listing_id = ? AND revoked_at IS NULL ORDER BY id', (listing_id,))]

    def revoke_approval(self, approval_id: int) -> None:
        with self.connect(write=True) as connection:
            if not connection.execute('UPDATE approvals SET revoked_at = ? WHERE id = ?',
                                      (datetime.now(timezone.utc).isoformat(), approval_id)).rowcount:
                raise KeyError('Approval not found.')

    def agree_viewing(self, listing_id: str, time: object, details: object, *, viewing_id: str | None = None) -> str:
        when = viewing_time(time)
        if not isinstance(details, str):
            raise ValueError('Viewing details must be text.')
        start = datetime.fromisoformat(when)
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            for other in select(connection, "SELECT * FROM viewings WHERE status IN ('agreed', 'booked') AND id IS NOT ?", (viewing_id,)):
                other_time = cast(str, other['time'])
                if abs(datetime.fromisoformat(other_time) - start) < timedelta(minutes=VIEWING_MINUTES):
                    raise ViewingConflict(f'The viewing of {cast(str, other["listing_id"])} at {other_time} overlaps {when}.')
            if viewing_id:
                if not connection.execute('''UPDATE viewings SET time = ?, details = ?, status = 'agreed'
                    WHERE id = ? AND listing_id = ? AND status IN ('agreed', 'booked')''', (when, details, viewing_id, listing_id)).rowcount:
                    raise KeyError('No agreed or booked viewing with that id for this listing.')
                return viewing_id
            viewing_id = str(uuid4())
            _ = connection.execute("INSERT INTO viewings (id, listing_id, time, details, status) VALUES (?, ?, ?, ?, 'agreed')",
                                   (viewing_id, listing_id, when, details))
            return viewing_id

    def agreed_viewings(self) -> list[ViewingRecord]:
        with self.connect() as connection:
            return [cast(ViewingRecord, dict(row)) for row in select(connection, "SELECT * FROM viewings WHERE status = 'agreed' ORDER BY time, id")]

    def scheduled_viewings(self) -> list[ViewingRecord]:
        with self.connect() as connection:
            return [cast(ViewingRecord, dict(row))
                    for row in select(connection, "SELECT * FROM viewings WHERE status IN ('agreed', 'booked') ORDER BY time, id")]

    def canceled_viewing_events(self) -> list[ViewingRecord]:
        with self.connect() as connection:
            return [cast(ViewingRecord, dict(row)) for row in select(
                connection, "SELECT * FROM viewings WHERE status = 'canceled' AND calendar_event_id IS NOT NULL ORDER BY time, id")]

    def record_booking(self, viewing_id: str, time: str, details: str, calendar_event_id: object) -> bool:
        """Books only a viewing still agreed with the time and details the event was written with, so a move that arrives
        during the calendar write stays agreed for the next write. A viewing canceled meanwhile takes the identifier of
        the event just written, so that event is removed instead of left behind."""
        _ = require_text(calendar_event_id, 'Calendar event id', limit=1000)
        with self.connect(write=True) as connection:
            if connection.execute('''UPDATE viewings SET status = 'booked', calendar_event_id = ?
                WHERE id = ? AND status = 'agreed' AND time = ? AND details = ?''', (calendar_event_id, viewing_id, time, details)).rowcount:
                return True
            _ = connection.execute("UPDATE viewings SET calendar_event_id = ? WHERE id = ? AND status = 'canceled'", (calendar_event_id, viewing_id))
            return False

    def record_event_removed(self, viewing_id: str, calendar_event_id: str) -> bool:
        with self.connect(write=True) as connection:
            return bool(connection.execute('''UPDATE viewings SET calendar_event_id = NULL
                WHERE id = ? AND status = 'canceled' AND calendar_event_id = ?''', (viewing_id, calendar_event_id)).rowcount)

    def cancel_viewing(self, viewing_id: str, *, possible_event_id: object) -> None:
        """A viewing without a recorded event may still have one, written by a create whose answer was lost, so it keeps
        `possible_event_id` for the removal, which counts an event that does not exist as removed."""
        _ = require_text(possible_event_id, 'Calendar event id', limit=1000)
        with self.connect(write=True) as connection:
            if not connection.execute('''UPDATE viewings SET status = 'canceled', calendar_event_id = CASE WHEN status = 'canceled'
                THEN calendar_event_id ELSE COALESCE(calendar_event_id, ?) END WHERE id = ?''', (possible_event_id, viewing_id)).rowcount:
                raise KeyError('Viewing not found.')

    @staticmethod
    def require_listing(connection: sqlite3.Connection, listing_id: str) -> None:
        if not select_one(connection, 'SELECT 1 FROM listings WHERE id = ?', (listing_id,)):
            raise KeyError('Listing not found.')
