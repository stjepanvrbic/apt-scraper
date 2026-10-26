import json
import math
from collections import Counter
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from code_features import FEATURES, configured_transit_feed, store_all_code_features, store_code_features
from schema import MIGRATIONS, content_fingerprint, migrate
from queries import RANKABLE, RANKING_EXCLUSION, answered_pairs, browse, paired, snapshot, user_noted, validate_page
from record import SEED_TAGS, known_spelling, validate_extraction, validate_judgments
from scoring import reading_vocabulary, stated_phrases, validate_note_reading, validate_preference_reading


UNCHANGED = object()
CONTACT_CHANNELS = {'email', 'imessage', 'contact_form'}
RENTAL_PERIODS = ('day', 'week', 'month', 'year', 'total')
WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
VIEWING_MINUTES = 60
CRITERIA = ('location', 'price', 'size', 'rooms', 'move_in', 'other')
PREFERENCES = ('likes', 'dislikes')
PLACE_FIELDS = ('name', 'address')
IMPORTANCE = ('low', 'medium', 'high')
READ_ATTEMPTS = 3


class TimeZoneDataMissing(RuntimeError):
    pass


class ViewingConflict(ValueError):
    pass


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
    if listing['rental_period'] not in (None, *RENTAL_PERIODS):
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


def require_object(value, name, keys):
    if not isinstance(value, dict) or not set(value) <= set(keys):
        raise ValueError(f'{name} must be an object with only {", ".join(keys)}.')


def require_texts(value, name):
    if not isinstance(value, list):
        raise ValueError(f'{name} must be an array of text.')
    for item in value:
        require_text(item, name, limit=1000)


def require_date(value, name):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError(f'{name} must use YYYY-MM-DD.')


def require_range(value, name, units=()):
    require_object(value, name, ('min', 'max', *units))
    bounds = [value.get('min'), value.get('max')]
    for bound in bounds:
        if bound is not None and (type(bound) not in (int, float) or not math.isfinite(bound) or bound < 0):
            raise ValueError(f'{name} limits must be nonnegative numbers or null.')
    if None not in bounds and bounds[0] > bounds[1]:
        raise ValueError(f'{name} minimum exceeds its maximum.')
    return bounds != [None, None]


def validate_criteria(criteria):
    require_object(criteria, 'Criteria', CRITERIA)
    if 'location' in criteria:
        require_texts(criteria['location'], 'Location')
        if not criteria['location']:
            raise ValueError('Location needs at least one area.')
    if 'price' in criteria:
        price = criteria['price']
        limited = require_range(price, 'Price', ('currency', 'period'))
        if (limited or 'currency' in price) and not (isinstance(price.get('currency'), str) and re.fullmatch(r'[A-Z]{3}', price['currency'])):
            raise ValueError('A price limit needs a three-letter currency code.')
        if (limited or 'period' in price) and price.get('period') not in RENTAL_PERIODS:
            raise ValueError(f'A price limit needs a period: {", ".join(RENTAL_PERIODS)}.')
    if 'size' in criteria:
        size = criteria['size']
        if (require_range(size, 'Size', ('unit',)) or 'unit' in size) and size.get('unit') not in ('m2', 'sqft'):
            raise ValueError('A size limit needs the unit m2 or sqft.')
    if 'rooms' in criteria:
        require_range(criteria['rooms'], 'Rooms')
    if criteria.get('move_in') is not None:
        require_date(criteria['move_in'], 'Move-in date')
    if 'other' in criteria:
        require_texts(criteria['other'], 'Other criteria')


def validate_preferences(preferences):
    require_object(preferences, 'Preferences', PREFERENCES)
    for key, value in preferences.items():
        require_texts(value, key.capitalize())


def validate_listing_sites(sites):
    if not isinstance(sites, list):
        raise ValueError('Listing sites must be an array of URLs.')
    for url in sites:
        require_url(url)
    if len(set(sites)) != len(sites):
        raise ValueError('Each listing site is named once.')


def validate_viewing_availability(availability):
    require_object(availability, 'Viewing availability', ('timezone', 'weekly', 'unavailable'))
    if 'timezone' in availability:
        try:
            ZoneInfo(availability['timezone'])
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            if not available_timezones():
                raise TimeZoneDataMissing(f'{sys.executable} has no time zone database, so the time zone cannot be checked. '
                                          'Run the script with a Python that has the tzdata package, such as the one Hermes runs on.') from None
            raise ValueError('Timezone must be an IANA time zone such as Europe/Berlin.') from None
    if not isinstance(availability.get('weekly', []), list) or not isinstance(availability.get('unavailable', []), list):
        raise ValueError('Weekly and unavailable must be arrays.')
    for window in availability.get('weekly', []):
        if not isinstance(window, dict) or set(window) != {'day', 'start', 'end'} or window['day'] not in WEEKDAYS:
            raise ValueError(f'A weekly window needs a day ({", ".join(WEEKDAYS)}), a start and an end.')
        if not all(isinstance(window[key], str) and re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', window[key]) for key in ('start', 'end')):
            raise ValueError('Window times use 24-hour HH:MM.')
        if window['start'] >= window['end']:
            raise ValueError('A window must end after it starts.')
    for period in availability.get('unavailable', []):
        if not isinstance(period, dict) or set(period) != {'from', 'to'}:
            raise ValueError('An unavailable period needs from and to dates.')
        require_date(period['from'], 'Unavailable from')
        require_date(period['to'], 'Unavailable to')
        if period['from'] > period['to']:
            raise ValueError('An unavailable period must end on or after its start.')


def validate_places(places):
    if not isinstance(places, list):
        raise ValueError('Places must be an array.')
    for place in places:
        if not isinstance(place, dict) or set(place) != set(PLACE_FIELDS):
            raise ValueError(f'A place has exactly {", ".join(PLACE_FIELDS)}.')
        require_text(place['name'], 'Place name', limit=200)
        require_text(place['address'], 'Place address', limit=1000)
    if len({place['name'].strip().casefold() for place in places}) != len(places):
        raise ValueError('Each place is named once.')


def require_importance(value, name):
    if not isinstance(value, str) or value not in IMPORTANCE:
        raise ValueError(f'The importance of {name} is {", ".join(IMPORTANCE)}.')


def validate_location_priorities(priorities):
    require_object(priorities, 'Location priorities', ('places', 'rapid_transit', 'areas'))
    for key in ('places', 'rapid_transit'):
        if key in priorities:
            require_importance(priorities[key], key)
    if not isinstance(priorities.get('areas', {}), dict):
        raise ValueError('Location priority areas must be an object from each area of the criteria to its importance.')
    for area, importance in priorities.get('areas', {}).items():
        require_importance(importance, area)


def reconcile_area_priorities(saved, changed):
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


SETTINGS = {
    'criteria': validate_criteria,
    'preferences': validate_preferences,
    'listing_sites': validate_listing_sites,
    'viewing_availability': validate_viewing_availability,
    'places': validate_places,
    'location_priorities': validate_location_priorities,
    'transit_feed': require_url,
}


def store_path(sample=False):
    if os.environ.get('HERMES_HOME'):
        directory = Path(os.environ['HERMES_HOME']) / 'workspace' / 'gallery'
    elif sys.platform == 'win32':
        directory = Path(os.environ['LOCALAPPDATA']) / 'apt-scraper'
    else:
        directory = Path.home() / 'Library' / 'Application Support' / 'apt-scraper'
    return directory / ('sample.sqlite3' if sample else 'listings.sqlite3')


def viewing_time(value):
    try:
        time = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError('Viewing time must be an ISO 8601 date and time with its UTC offset.') from None
    if time.utcoffset() is None:
        raise ValueError('Viewing time must include its UTC offset.')
    return time.astimezone(timezone.utc).isoformat()


def validate_agent(agent):
    if not isinstance(agent, dict) or not set(agent) <= {'name', 'email', 'phone', 'contact_form'}:
        raise ValueError('Agent accepts name, email, phone and contact_form.')
    for name, value in agent.items():
        require_text(value, name, limit=1000)
        if name == 'contact_form':
            require_url(value)


def extraction_read(listing_id, fingerprint):
    """The model_reads key of the extraction of one listing content."""
    return json.dumps(['extraction', listing_id, fingerprint])


def explain_pending_rankings(connection, listings):
    pending = {listing['id']: listing for listing in listings if not listing['analysis_current'] or listing['rank'] is None}
    if not pending:
        return
    placeholders = ','.join('?' for _ in pending)
    rows = connection.execute(f'''SELECT l.id, l.fingerprint, records.listing_id AS record,
        {RANKING_EXCLUSION} AS exclusion FROM listings l
        LEFT JOIN feedback f ON f.listing_id = l.id
        LEFT JOIN feature_records records ON records.listing_id = l.id AND records.fingerprint = l.fingerprint
        WHERE l.id IN ({placeholders})''', list(pending)).fetchall()
    reads = [extraction_read(row['id'], row['fingerprint']) for row in rows]
    attempts = dict(connection.execute(f'SELECT read, attempts FROM model_reads WHERE read IN ({placeholders})', reads))
    for row, read in zip(rows, reads):
        listing = pending[row['id']]
        if row['exclusion']:
            reason = row['exclusion']
        elif row['record']:
            reason = 'Not ranked yet: new or changed since the last rerank.'
        elif attempts.get(read, 0) >= READ_ATTEMPTS:
            reason = f'Not ranked yet: extraction failed {READ_ATTEMPTS} times. Score and rerank to use code-derived features.'
        else:
            reason = 'Not ranked yet: waiting for its feature record.'
        listing.update(rank=None, reason=reason, exploration=False)


def apply_criteria(connection, judge):
    row = connection.execute("SELECT value FROM settings WHERE name = 'criteria'").fetchone()
    if not row:
        return None
    criteria = json.loads(row['value'])
    rows = connection.execute('SELECT l.id, CASE WHEN r.fingerprint = l.fingerprint THEN r.features END AS features '
                              'FROM listings l LEFT JOIN ranking r ON r.listing_id = l.id ORDER BY l.rowid').fetchall()
    verdicts = {row['id']: judge(criteria, json.loads(row['features']) if row['features'] else None) for row in rows}
    connection.executemany('UPDATE listings SET matches_criteria = ? WHERE id = ? AND matches_criteria != ?',
                           [(verdict == 'inside', identifier, verdict == 'inside') for identifier, verdict in verdicts.items()])
    return dict(Counter(verdicts.values()))


class ListingStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as connection:
            migrate(connection)

    @property
    def schema_version(self):
        with self._open() as connection:
            return connection.execute('PRAGMA user_version').fetchone()[0]

    @contextmanager
    def _open(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def connect(self, write=False):
        with self._open() as connection:
            if write:
                connection.execute('BEGIN IMMEDIATE')
            # A process that opened the store before another process migrated it would write in a schema it does not know.
            if connection.execute('PRAGMA user_version').fetchone()[0] != len(MIGRATIONS):
                raise ValueError('This database was migrated to another schema after the gallery opened it. Open the gallery again.')
            before = connection.total_changes
            yield connection
            if write and connection.total_changes != before:
                connection.execute('UPDATE changes SET revision = revision + 1 WHERE id = 1')

    def upsert_listing(self, listing, *, matches_criteria=None):
        listing = validate_listing(listing)
        if matches_criteria is not None and type(matches_criteria) is not bool:
            raise ValueError('Criteria eligibility must be true or false.')
        with self.connect(write=True) as connection:
            previous = connection.execute('SELECT * FROM listings WHERE id = ?', (listing['id'],)).fetchone()
            if previous:
                listing['first_seen'] = previous['first_seen']
            fingerprint = content_fingerprint(listing)
            changed = not previous or previous['fingerprint'] != fingerprint
            analysis = connection.execute('SELECT * FROM ranking WHERE listing_id = ?', (listing['id'],)).fetchone()
            needs_analysis = not analysis or analysis['fingerprint'] != fingerprint or analysis['features'] is None
            if not previous or changed or previous['status'] != listing['status']:
                connection.execute(
                    '''INSERT INTO listings (id, data, status, first_seen, fingerprint) VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET data = excluded.data, status = excluded.status, fingerprint = excluded.fingerprint''',
                    (listing['id'], json.dumps(listing, ensure_ascii=False), listing['status'], listing['first_seen'], fingerprint),
                )
            if changed:
                store_code_features(connection, listing['id'])
            if matches_criteria is not None:
                connection.execute('UPDATE listings SET matches_criteria = ? WHERE id = ? AND matches_criteria != ?',
                                   (matches_criteria, listing['id'], matches_criteria))
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

    def update_settings(self, *, judge=None, **settings):
        """Saving criteria applies them to every stored listing in the same transaction, with judge as in apply_criteria.
        Saving places or the transit feed computes every listing's distances again, to the places with stored geocodes."""
        if not settings or not set(settings) <= set(SETTINGS):
            raise ValueError(f'Settings are {", ".join(SETTINGS)}, and at least one is required.')
        for name, value in settings.items():
            SETTINGS[name](value)
        if judge is not None and 'criteria' not in settings:
            raise ValueError('A judge is given only with the criteria it applies.')
        with self.connect(write=True) as connection:
            if 'criteria' in settings and judge is None and connection.execute('SELECT 1 FROM listings LIMIT 1').fetchone():
                raise ValueError('Stored listings must be judged against new criteria. Give a judge.')
            saved = {row['name']: json.loads(row['value']) for row in connection.execute('SELECT * FROM settings')}
            settings = {**settings, **reconcile_area_priorities({**saved, **settings}, settings)}
            connection.executemany('INSERT INTO settings VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value',
                                   [(name, json.dumps(value, allow_nan=False)) for name, value in settings.items()])
            if {'places', 'transit_feed'} & set(settings):
                store_all_code_features(connection)
            return apply_criteria(connection, judge) if 'criteria' in settings and judge else None

    def apply_criteria(self, judge):
        """Shows each listing that judge(criteria, features) finds 'inside' the stored criteria and hides every other one.
        features is the listing's current analysis or None. Returns the count of each verdict, or None without criteria."""
        with self.connect(write=True) as connection:
            return apply_criteria(connection, judge)

    def save_geocodes(self, geocodes):
        """Keeps the coordinates a geocoder found for place addresses, by address: {latitude, longitude, found}, where
        found is how the geocoder named the location."""
        now = datetime.now(timezone.utc).isoformat()
        with self.connect(write=True) as connection:
            connection.executemany('''INSERT INTO geocodes VALUES (?, ?, ?, ?, ?) ON CONFLICT(address) DO UPDATE SET
                latitude = excluded.latitude, longitude = excluded.longitude, found = excluded.found, geocoded_at = excluded.geocoded_at''',
                                   [(address, found['latitude'], found['longitude'], found['found'], now) for address, found in geocodes.items()])

    def geocodes(self, addresses):
        """The stored coordinates of each of the addresses that has them: {latitude, longitude, found}, by address."""
        with self.connect() as connection:
            return {row['address']: {'latitude': row['latitude'], 'longitude': row['longitude'], 'found': row['found']}
                    for address in addresses for row in connection.execute('SELECT * FROM geocodes WHERE address = ?', (address,))}

    def transit_feed(self):
        """The rapid-transit feed the settings name, the default without one, and the stations fetched from it:
        {url, fetched_at, stations}, with fetched_at None and no stations until they are fetched."""
        with self.connect() as connection:
            url = configured_transit_feed(connection)
            fetched = connection.execute('SELECT * FROM transit_feed WHERE url = ?', (url,)).fetchone()
            stations = [dict(row) for row in connection.execute('SELECT * FROM stations ORDER BY id')] if fetched else []
            return {'url': url, 'fetched_at': fetched['fetched_at'] if fetched else None, 'stations': stations}

    def save_stations(self, url, stations):
        """Replaces the stored rapid-transit stations with those fetched from the feed at url, each {id, name, latitude,
        longitude}, and computes every listing's distances again."""
        with self.connect(write=True) as connection:
            connection.execute('DELETE FROM stations')
            connection.executemany('INSERT INTO stations VALUES (?, ?, ?, ?)',
                                   [(station['id'], station['name'], station['latitude'], station['longitude']) for station in stations])
            connection.execute('INSERT INTO transit_feed VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET url = excluded.url, fetched_at = excluded.fetched_at',
                               (url, datetime.now(timezone.utc).isoformat()))
            store_all_code_features(connection)

    def places_without_coordinates(self):
        """The names of the saved places whose address has no stored geocode, such as places saved before places were
        geocoded. They count for no listing until they are saved again."""
        places = self.settings().get('places', [])
        located = self.geocodes({place['address'] for place in places})
        return [place['name'] for place in places if place['address'] not in located]

    def missing_settings(self, names=tuple(SETTINGS)):
        """What the interview still needs, limited to the settings in names."""
        saved = self.settings()
        missing = [f'{name}.{key}' for name, keys in (('criteria', CRITERIA), ('preferences', PREFERENCES))
                   for key in keys if key not in saved.get(name, {})]
        if not saved.get('listing_sites'):
            missing.append('listing_sites')
        availability = saved.get('viewing_availability', {})
        missing += [f'viewing_availability.{key}' for key in ('timezone', 'weekly') if not availability.get(key)]
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

    def analyze(self, listing_id, fingerprint, features):
        if not isinstance(features, dict):
            raise ValueError('Features must be an object.')
        features = json.dumps(features, allow_nan=False)
        with self.connect(write=True) as connection:
            self.require_fingerprint(connection, listing_id, fingerprint)
            connection.execute('''INSERT INTO ranking (listing_id, fingerprint, features) VALUES (?, ?, ?)
                ON CONFLICT(listing_id) DO UPDATE SET fingerprint = excluded.fingerprint, features = excluded.features,
                rank = CASE WHEN ranking.fingerprint = excluded.fingerprint THEN ranking.rank END,
                reason = CASE WHEN ranking.fingerprint = excluded.fingerprint THEN ranking.reason END,
                explanation = CASE WHEN ranking.fingerprint = excluded.fingerprint THEN ranking.explanation END,
                exploration = ranking.exploration AND ranking.fingerprint = excluded.fingerprint''', (listing_id, fingerprint, features))
            store_code_features(connection, listing_id)

    def features(self, listing_id):
        with self.connect() as connection:
            self.require_listing(connection, listing_id)
            row = connection.execute('SELECT features FROM ranking JOIN listings ON listings.id = ranking.listing_id WHERE listing_id = ? AND ranking.fingerprint = listings.fingerprint', (listing_id,)).fetchone()
            return json.loads(row['features']) if row and row['features'] else None

    def code_features(self, today=None):
        """The code-derived features of every listing's current content, as code_features.py describes them, by listing id,
        with the content fingerprint, first_seen and days_since_first_seen counted to today, a date (by default today in UTC)."""
        today = today or datetime.now(timezone.utc).date()
        with self.connect() as connection:
            return {row['listing_id']: {**{name: row[name] for name in ('fingerprint', *FEATURES, 'first_seen')},
                                        'place_distances_km': json.loads(row['place_distances_km']),
                                        'days_since_first_seen': (today - date.fromisoformat(row['first_seen'])).days}
                    for row in connection.execute('''SELECT c.*, l.first_seen FROM code_features c
                        JOIN listings l ON l.id = c.listing_id AND l.fingerprint = c.fingerprint ORDER BY c.listing_id''')}

    def ranking_inputs(self, *, include_signals=False):
        """Everything a rerank reads, from one snapshot of the store: each listing with its current feature record or
        None, its judgments of stated preferences, its code-derived features, the criteria areas whose search found it
        or None, whether extraction gave up on it, whether it is ranked (available, inside the criteria and not
        dismissed), its rank from the latest rerank when its content has not changed since, and the user's actions on it; the settings; the readings of the stated preferences by (kind,
        phrase) and of the user's notes by note id; each listing's latest note sentiment; and the pairwise answers as
        (chosen, other). Completed visits are included only for the rerank's diagnostic check."""
        with self.connect() as connection:
            connection.execute('BEGIN')
            settings = {row['name']: json.loads(row['value']) for row in connection.execute('SELECT * FROM settings')}
            attempts = {row['read']: row['attempts'] for row in connection.execute('SELECT * FROM model_reads')}
            records = {row['listing_id']: row for row in connection.execute(
                'SELECT r.* FROM feature_records r JOIN listings l ON l.id = r.listing_id AND l.fingerprint = r.fingerprint')}
            code = {row['listing_id']: {**dict(row), 'place_distances_km': json.loads(row['place_distances_km'])} for row in connection.execute(
                'SELECT c.* FROM code_features c JOIN listings l ON l.id = c.listing_id AND l.fingerprint = c.fingerprint')}
            ranking = {row['listing_id']: row for row in connection.execute(
                'SELECT r.* FROM ranking r JOIN listings l ON l.id = r.listing_id AND l.fingerprint = r.fingerprint')}
            facts = {identifier: json.loads(row['features']).get('facts', {}) for identifier, row in ranking.items() if row['features']}
            feedback = {row['listing_id']: row for row in connection.execute('SELECT * FROM feedback')}
            contacts = {row['listing_id'] for row in connection.execute('SELECT listing_id FROM contacts')}
            noted = user_noted(connection)
            note_readings = {row['note_id']: (row['listing_id'], json.loads(row['reading'])) for row in connection.execute(
                'SELECT r.*, n.listing_id FROM note_readings r JOIN notes n ON n.id = r.note_id ORDER BY r.note_id')}
            preference_readings = {(row['kind'], row['phrase']): json.loads(row['features'])
                                   for row in connection.execute('SELECT * FROM preference_readings')}
            pairs = answered_pairs(connection)
            visits = []
            if include_signals:
                visits = [dict(row) for row in connection.execute('''SELECT s.listing_id, s.visit_id,
                    SUM(COALESCE(s.duration_ms, 0)) AS duration_ms FROM implicit_signals s
                    JOIN listings l ON l.id = s.listing_id AND l.fingerprint = s.fingerprint
                    GROUP BY s.listing_id, s.fingerprint, s.visit_id
                    HAVING SUM(s.kind = 'open') > 0 AND SUM(s.kind = 'close') > 0''')]
            compared = paired(pairs)
            listings = []
            for row in connection.execute(f'''SELECT l.*, {RANKABLE} AS rankable FROM listings l
                    LEFT JOIN feedback f ON f.listing_id = l.id ORDER BY l.id'''):
                data, identifier = json.loads(row['data']), row['id']
                record = records.get(identifier)
                choice = feedback.get(identifier)
                dismissed = bool(choice and choice['dismissed'])
                listings.append({
                    'id': identifier, 'fingerprint': row['fingerprint'], 'title': data.get('title'), 'address': data.get('address'),
                    'rankable': bool(row['rankable']),
                    'record': json.loads(record['extraction']) if record else None,
                    'judgments': json.loads(record['preferences']) if record else {},
                    'code': code.get(identifier, {}),
                    'areas': facts[identifier].get('areas') if identifier in facts else None,
                    'given_up': not record and attempts.get(extraction_read(identifier, row['fingerprint']), 0) >= READ_ATTEMPTS,
                    'rank': ranking[identifier]['rank'] if identifier in ranking else None,
                    'favorite': bool(choice and choice['favorite']), 'dismissed': dismissed, 'contact': identifier in contacts,
                    'user_notes': identifier in noted, 'paired': identifier in compared,
                })
        return {'listings': listings, 'settings': settings, 'preference_readings': preference_readings,
                'note_readings': {note: reading for note, (_, reading) in note_readings.items()},
                'sentiments': {listing_id: reading['sentiment'] for listing_id, reading in note_readings.values()}, 'pairs': pairs,
                'implicit_visits': visits}

    def record_signals(self, events):
        """Stores an atomic, retryable batch without changing the gallery's content revision."""
        if not isinstance(events, list) or not 1 <= len(events) <= 20:
            raise ValueError('Signals must contain 1 to 20 events.')
        fields = {'event_id', 'visit_id', 'listing_id', 'fingerprint', 'kind', 'occurred_at'}
        rows = []
        for event in events:
            if not isinstance(event, dict) or event.get('kind') not in ('open', 'dwell', 'photo', 'close'):
                raise ValueError('Signal kind must be open, dwell, photo or close.')
            extra = {'duration_ms'} if event['kind'] == 'dwell' else {'photo_url'} if event['kind'] == 'photo' else set()
            if set(event) != fields | extra:
                raise ValueError('Signal fields do not match its kind.')
            for name in fields:
                require_text(event[name], name, limit=200)
            if not re.fullmatch('[a-f0-9]{64}', event['fingerprint']):
                raise ValueError('Signal fingerprint must identify listing content.')
            try:
                occurred = datetime.fromisoformat(event['occurred_at'])
            except ValueError:
                raise ValueError('Signal occurred_at must be an ISO timestamp.') from None
            if occurred.tzinfo is None:
                raise ValueError('Signal occurred_at must include a timezone.')
            duration = event.get('duration_ms')
            photo = event.get('photo_url')
            if event['kind'] == 'dwell' and (type(duration) is not int or not 0 <= duration <= 86400000):
                raise ValueError('Signal duration_ms must be an integer from 0 to 86400000.')
            if event['kind'] == 'photo':
                require_text(photo, 'photo_url')
                if not re.fullmatch(r'/media/[A-Za-z0-9._-]+', photo):
                    require_url(photo)
            rows.append((event['event_id'], event['visit_id'], event['listing_id'], event['fingerprint'],
                         event['kind'], event['occurred_at'], duration, photo))
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            recorded = 0
            for row in rows:
                self.require_listing(connection, row[2])
                previous = connection.execute('SELECT * FROM implicit_signals WHERE event_id = ?', (row[0],)).fetchone()
                if previous:
                    if tuple(previous) != row:
                        raise ValueError('Signal event_id already records a different event.')
                    continue
                connection.execute('INSERT INTO implicit_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?)', row)
                recorded += 1
            return recorded

    def save_rerank(self, result):
        """Replaces the whole ranking in one transaction with a rerank's result: each listing in result['ranked']
        gets its rank, reason, explanation and exploration mark, each in result['unranked'] its reason without a rank,
        and every other listing loses its rank, reason and explanation. The summary is kept as the latest rerank, with
        the ids of the entries left out because their listing changed meanwhile as `stale`. The rerank's review pass
        waits from then on, with result['check'], the scores its leave-one-out check reads, and a review still waiting
        for an earlier rerank is superseded. Returns the rerank's id."""
        with self.connect(write=True) as connection:
            current = dict(connection.execute('SELECT id, fingerprint FROM listings').fetchall())
            entries = [(identifier, entry['fingerprint'], entry['rank'], require_text(entry['reason'], 'Reason', limit=500),
                        json.dumps(entry['explanation'], ensure_ascii=False, allow_nan=False), int(entry['exploration']))
                       for identifier, entry in result['ranked'].items()]
            entries += [(identifier, entry['fingerprint'], None, require_text(entry['reason'], 'Reason', limit=500), None, 0)
                        for identifier, entry in result['unranked'].items()]
            stale = sorted(entry[0] for entry in entries if current.get(entry[0]) != entry[1])
            fresh = [entry for entry in entries if entry[0] not in stale]
            connection.execute('UPDATE ranking SET rank = NULL, reason = NULL, explanation = NULL, exploration = 0')
            connection.executemany('''INSERT INTO ranking (listing_id, fingerprint, rank, reason, explanation, exploration) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(listing_id) DO UPDATE SET rank = excluded.rank, reason = excluded.reason, explanation = excluded.explanation,
                exploration = excluded.exploration, features = CASE WHEN ranking.fingerprint = excluded.fingerprint THEN ranking.features END,
                fingerprint = excluded.fingerprint''', fresh)
            now = datetime.now(timezone.utc).isoformat()
            rerank_id = connection.execute('INSERT INTO reranks (finished_at, result) VALUES (?, ?)',
                                           (now, json.dumps({**result['summary'], 'stale': stale}, ensure_ascii=False, allow_nan=False))).lastrowid
            connection.execute("UPDATE reviews SET state = 'superseded', snapshot = NULL, updated_at = ? WHERE state IN ('waiting', 'running')", (now,))
            connection.execute("INSERT INTO reviews (rerank_id, state, updated_at, snapshot) VALUES (?, 'waiting', ?, ?)",
                               (rerank_id, now, json.dumps(result['check'], allow_nan=False)))
            return rerank_id

    def last_rerank(self):
        """The latest rerank as rerank describes it, or None before the first."""
        return self.rerank(None)

    def rerank(self, rerank_id):
        """The summary of a rerank, the latest when rerank_id is None, with its id, the time it finished and its review
        pass: the review's state, the times it was handed to the agent, when its state last changed, and its result once
        it has one. The states are waiting, running (handed to the agent), done, failed and superseded (a later rerank
        replaced it). None when there is no such rerank."""
        with self.connect() as connection:
            row = connection.execute('SELECT r.*, v.state, v.attempts, v.updated_at, v.result AS review FROM reranks r '
                                     'LEFT JOIN reviews v ON v.rerank_id = r.id WHERE ? IS NULL OR r.id = ? ORDER BY r.id DESC LIMIT 1',
                                     (rerank_id, rerank_id)).fetchone()
            if not row:
                return None
            review = {'state': row['state'], 'attempts': row['attempts'], 'updated_at': row['updated_at'],
                      'result': json.loads(row['review']) if row['review'] else None} if row['state'] else None
            return {**json.loads(row['result']), 'id': row['id'], 'finished_at': row['finished_at'], 'review': review}

    def review_snapshot(self, rerank_id):
        """The scores the leave-one-out check of a waiting review reads, as the rerank stored them, or None once the
        review ended."""
        with self.connect() as connection:
            row = connection.execute('SELECT snapshot FROM reviews WHERE rerank_id = ?', (rerank_id,)).fetchone()
            return json.loads(row['snapshot']) if row and row['snapshot'] else None

    def ranked_listings(self):
        """Each listing the latest rerank ranked whose content has not changed since: its rank, reason, explanation and
        exploration mark, by id."""
        with self.connect() as connection:
            return {row['listing_id']: {'rank': row['rank'], 'reason': row['reason'], 'explanation': json.loads(row['explanation']),
                                        'exploration': bool(row['exploration'])}
                    for row in connection.execute('SELECT r.* FROM ranking r JOIN listings l ON l.id = r.listing_id AND l.fingerprint = r.fingerprint '
                                                  'WHERE r.rank IS NOT NULL ORDER BY r.rank')}

    def hand_review(self, rerank_id):
        """Records that the review of a rerank was handed to the agent once more, and returns how many times it was, or
        None when the review no longer waits, because a later rerank replaced it or it ended meanwhile."""
        with self.connect(write=True) as connection:
            if not connection.execute("UPDATE reviews SET state = 'running', attempts = attempts + 1, updated_at = ? "
                                      "WHERE rerank_id = ? AND state IN ('waiting', 'running')",
                                      (datetime.now(timezone.utc).isoformat(), rerank_id)).rowcount:
                return None
            return connection.execute('SELECT attempts FROM reviews WHERE rerank_id = ?', (rerank_id,)).fetchone()['attempts']

    def fail_review(self, rerank_id, error):
        """Ends a waiting review without a saved review, with the error saying why."""
        with self.connect(write=True) as connection:
            connection.execute("UPDATE reviews SET state = 'failed', snapshot = NULL, result = ?, updated_at = ? "
                               "WHERE rerank_id = ? AND state IN ('waiting', 'running')",
                               (json.dumps({'error': error}, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), rerank_id))

    def save_review(self, rerank_id, ranks, reasons, result):
        """Applies a saved review in one transaction: each listing in ranks gets its rank and each in reasons its
        reason, and the review is done with result. A listing whose content changed since the rerank keeps no rank, as
        after any rerank. Raises ValueError when a later rerank replaced the review or it ended."""
        with self.connect(write=True) as connection:
            review = connection.execute('SELECT state FROM reviews WHERE rerank_id = ?', (rerank_id,)).fetchone()
            if not review or review['state'] not in ('waiting', 'running'):
                raise ValueError(f'The review of rerank {rerank_id} no longer waits: a later rerank replaced it or it ended.')
            current = {row['listing_id'] for row in connection.execute(
                'SELECT r.listing_id FROM ranking r JOIN listings l ON l.id = r.listing_id AND l.fingerprint = r.fingerprint WHERE r.rank IS NOT NULL')}
            connection.executemany('UPDATE ranking SET rank = ? WHERE listing_id = ?',
                                   [(rank, identifier) for identifier, rank in ranks.items() if identifier in current])
            connection.executemany('UPDATE ranking SET reason = ? WHERE listing_id = ?',
                                   [(require_text(reason, 'Reason', limit=500), identifier) for identifier, reason in reasons.items() if identifier in current])
            connection.execute("UPDATE reviews SET state = 'done', snapshot = NULL, result = ?, updated_at = ? WHERE rerank_id = ?",
                               (json.dumps(result, ensure_ascii=False, allow_nan=False), datetime.now(timezone.utc).isoformat(), rerank_id))

    def reading_vocabulary(self, connection=None):
        """The features a reading may name: those of the feature record, the code features, the criteria areas and the
        places."""
        if connection is None:
            with self.connect() as connection:
                return self.reading_vocabulary(connection)
        settings = {row['name']: json.loads(row['value']) for row in connection.execute("SELECT * FROM settings WHERE name IN ('criteria', 'places')")}
        return reading_vocabulary(sorted(self.known_tags(connection)), settings.get('criteria', {}).get('location', []),
                                  [place['name'] for place in settings.get('places', [])])

    def unread_notes(self):
        """The user's notes without a reading, oldest first."""
        with self.connect() as connection:
            return [dict(row) for row in connection.execute('''SELECT n.id, n.listing_id, n.text, n.created_at
                FROM notes n LEFT JOIN note_readings r ON r.note_id = n.id
                WHERE n.role = 'user' AND r.note_id IS NULL ORDER BY n.id''')]

    def unread_preferences(self):
        """The stated likes and dislikes without a reading of their current wording, as (kind, phrase)."""
        with self.connect() as connection:
            settings = {row['name']: json.loads(row['value']) for row in connection.execute('SELECT * FROM settings')}
            read = {(row['kind'], row['phrase']) for row in connection.execute('SELECT kind, phrase FROM preference_readings')}
        return [item for item in stated_phrases(settings) if item not in read]

    def preference_readings(self):
        with self.connect() as connection:
            return {(row['kind'], row['phrase']): json.loads(row['features']) for row in connection.execute('SELECT * FROM preference_readings')}

    def note_readings(self):
        with self.connect() as connection:
            return {row['note_id']: json.loads(row['reading']) for row in connection.execute('SELECT * FROM note_readings')}

    def save_readings(self, *, notes=(), preferences=()):
        """Stores the model's readings, all in one transaction or none: notes as (note id, {sentiment, likes,
        dislikes}) for notes by the user, preferences as (kind, phrase, features) for the stated likes and dislikes,
        each feature {feature, direction}. Features must be in the reading vocabulary."""
        with self.connect(write=True) as connection:
            vocabulary = self.reading_vocabulary(connection)
            settings = {row['name']: json.loads(row['value']) for row in connection.execute('SELECT * FROM settings')}
            stated = set(stated_phrases(settings))
            now = datetime.now(timezone.utc).isoformat()
            rows = []
            for kind, phrase, features in preferences:
                if (kind, phrase) not in stated:
                    raise ValueError(f'"{phrase}" is not a stated {kind}.')
                try:
                    rows.append((kind, phrase, json.dumps(validate_preference_reading(features, vocabulary), ensure_ascii=False), now))
                except ValueError as error:
                    raise ValueError(f'The reading of "{phrase}": {error}') from None
            readings = []
            for note_id, reading in notes:
                note = connection.execute('SELECT role FROM notes WHERE id = ?', (note_id,)).fetchone()
                if not note or note['role'] != 'user':
                    raise ValueError(f'Note {note_id} is not a note by the user.')
                try:
                    readings.append((note_id, json.dumps(validate_note_reading(reading, vocabulary), ensure_ascii=False), now))
                except ValueError as error:
                    raise ValueError(f'The reading of note {note_id}: {error}') from None
            connection.executemany('''INSERT INTO preference_readings VALUES (?, ?, ?, ?) ON CONFLICT(kind, phrase)
                DO UPDATE SET features = excluded.features, read_at = excluded.read_at''', rows)
            connection.executemany('''INSERT INTO note_readings VALUES (?, ?, ?) ON CONFLICT(note_id)
                DO UPDATE SET reading = excluded.reading, read_at = excluded.read_at''', readings)

    def answer_pair(self, chosen, other):
        """Records the user's answer to a pairwise question: chosen over other."""
        with self.connect(write=True) as connection:
            self.require_pair(connection, chosen, other)
            previous = connection.execute('''SELECT chosen FROM pairwise_answers
                WHERE (chosen = ? AND other = ?) OR (chosen = ? AND other = ?)''', (chosen, other, other, chosen)).fetchone()
            if previous:
                if previous['chosen'] != chosen:
                    raise ValueError('This pair was already answered.')
                return
            connection.execute('INSERT INTO pairwise_answers (chosen, other, answered_at) VALUES (?, ?, ?)',
                               (chosen, other, datetime.now(timezone.utc).isoformat()))
            self.advance_pairwise_skips(connection)

    def require_pair(self, connection, first, second):
        require_text(first, 'First listing')
        require_text(second, 'Second listing')
        if first == second:
            raise ValueError('A pairwise question compares two different listings.')
        for identifier in (first, second):
            self.require_listing(connection, identifier)
            available = connection.execute(f'''SELECT 1 FROM listings l LEFT JOIN feedback f ON f.listing_id = l.id
                WHERE l.id = ? AND {RANKABLE}''',
                                           (identifier,)).fetchone()
            if not available:
                raise ValueError('This listing is no longer available to compare. Refresh Compare.')

    @staticmethod
    def advance_pairwise_skips(connection):
        connection.execute('DELETE FROM pairwise_skips WHERE remaining = 1')
        connection.execute('UPDATE pairwise_skips SET remaining = remaining - 1')

    def skip_pair(self, first, second):
        """Suppresses this pair until twenty other questions have been answered or skipped."""
        with self.connect(write=True) as connection:
            self.require_pair(connection, first, second)
            first, second = sorted((first, second))
            if connection.execute('SELECT 1 FROM pairwise_skips WHERE first = ? AND second = ?', (first, second)).fetchone():
                return
            if connection.execute('''SELECT 1 FROM pairwise_answers
                WHERE (chosen = ? AND other = ?) OR (chosen = ? AND other = ?)''', (first, second, second, first)).fetchone():
                raise ValueError('This pair was already answered.')
            self.advance_pairwise_skips(connection)
            connection.execute('INSERT INTO pairwise_skips VALUES (?, ?, 20)', (first, second))

    def skipped_pairs(self):
        with self.connect() as connection:
            return [tuple(row) for row in connection.execute('SELECT first, second FROM pairwise_skips')]

    def pairwise_answers(self):
        with self.connect() as connection:
            return answered_pairs(connection)

    def given_up_extractions(self):
        """The listings whose current content failed extraction READ_ATTEMPTS times and has no record. The ranking ranks
        them from their code features."""
        return {listing['id'] for listing in self.ranking_inputs()['listings'] if listing['given_up']}

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

    def save_extraction(self, listing_id, fingerprint, extraction, *, photos, phrases):
        """Stores the model's part of the feature record of this content when it keeps the contract of record.py.
        photos are the unique photo URLs the extraction numbered from 1, phrases the stated preferences it judged.
        A trait spelled like a known tag but for hyphens and spaces takes the known spelling. New traits and concerns
        join the tag list in the same transaction. Returns the new tags."""
        extraction = validate_extraction(extraction, len(photos), phrases)
        preferences = extraction.pop('preferences')
        extraction['photos'] = [{**photo, 'url': photos[photo['index'] - 1]} for photo in extraction['photos']]
        with self.connect(write=True) as connection:
            self.require_fingerprint(connection, listing_id, fingerprint)
            known = self.known_tags(connection)
            for field in ('traits', 'concerns'):
                extraction[field] = list(dict.fromkeys(known_spelling(tag, known) for tag in extraction[field]))
            new_tags = sorted({*extraction['traits'], *extraction['concerns']} - known)
            now = datetime.now(timezone.utc).isoformat()
            connection.executemany('INSERT INTO tags VALUES (?, ?)', [(tag, now) for tag in new_tags])
            connection.execute('''INSERT INTO feature_records VALUES (?, ?, ?, ?, ?) ON CONFLICT(listing_id) DO UPDATE SET
                fingerprint = excluded.fingerprint, extraction = excluded.extraction, extracted_at = excluded.extracted_at,
                preferences = CASE WHEN feature_records.fingerprint = excluded.fingerprint
                              THEN json_patch(feature_records.preferences, excluded.preferences) ELSE excluded.preferences END''',
                               (listing_id, fingerprint, json.dumps(extraction, ensure_ascii=False), now, json.dumps(preferences, ensure_ascii=False)))
            return {'new_tags': new_tags}

    def judge_preferences(self, entries):
        """Adds judgments of stated preferences made after the extraction of a listing's content, from its record and text.
        entries are (listing id, fingerprint, judgments, phrases judged). All are stored in one transaction, or none when
        any is invalid or its listing has no record of that content."""
        entries = [(listing_id, fingerprint, validate_judgments(judgments, phrases)) for listing_id, fingerprint, judgments, phrases in entries]
        with self.connect(write=True) as connection:
            for listing_id, fingerprint, judgments in entries:
                self.require_fingerprint(connection, listing_id, fingerprint)
                if not connection.execute('UPDATE feature_records SET preferences = json_patch(preferences, ?) WHERE listing_id = ? AND fingerprint = ?',
                                          (json.dumps(judgments, ensure_ascii=False), listing_id, fingerprint)).rowcount:
                    raise ValueError(f'{listing_id} has no feature record of its current content to judge preferences from.')

    def feature_records(self):
        """The feature record of every listing whose current content has one: its extraction, the preferences judged for
        it and when it was extracted."""
        return self.current_feature_records()

    def feature_record(self, listing_id):
        return self.current_feature_records(listing_id).get(listing_id)

    def current_feature_records(self, listing_id=None):
        with self.connect() as connection:
            return {row['listing_id']: {'extraction': json.loads(row['extraction']), 'preferences': json.loads(row['preferences']),
                                        'extracted_at': row['extracted_at']}
                    for row in connection.execute('''SELECT r.* FROM feature_records r JOIN listings l ON l.id = r.listing_id
                        AND l.fingerprint = r.fingerprint WHERE ? IS NULL OR r.listing_id = ?
                        ORDER BY r.listing_id''', (listing_id, listing_id))}

    def tags(self):
        """The tag list: the seed tags of record.py and every tag an extraction added."""
        with self.connect() as connection:
            return sorted(self.known_tags(connection))

    @staticmethod
    def known_tags(connection):
        return {*SEED_TAGS, *(row['tag'] for row in connection.execute('SELECT tag FROM tags'))}

    def read_attempts(self):
        """How many times each model read was handed to the agent without its result being saved."""
        with self.connect() as connection:
            return {row['read']: row['attempts'] for row in connection.execute('SELECT * FROM model_reads')}

    def record_read_attempts(self, failed, waiting):
        """Counts one more failed attempt for each read in failed and forgets every read not in waiting or failed."""
        with self.connect(write=True) as connection:
            keep = {*waiting, *failed}
            connection.executemany('DELETE FROM model_reads WHERE read = ?',
                                   [(read,) for (read,) in connection.execute('SELECT read FROM model_reads') if read not in keep])
            connection.executemany('INSERT INTO model_reads VALUES (?, 1) ON CONFLICT(read) DO UPDATE SET attempts = attempts + 1',
                                   [(read,) for read in failed])

    def reset_read_attempts(self):
        with self.connect(write=True) as connection:
            connection.execute('DELETE FROM model_reads')

    @staticmethod
    def require_fingerprint(connection, listing_id, fingerprint):
        ListingStore.require_listing(connection, listing_id)
        if connection.execute('SELECT fingerprint FROM listings WHERE id = ?', (listing_id,)).fetchone()[0] != fingerprint:
            raise ValueError('Listing content changed. Read the current listing before saving analysis or rank.')

    def listing(self, listing_id, *, notes_page=1, correspondence_page=1, viewings_page=1, visible_only=False):
        with self.connect() as connection:
            connection.execute('BEGIN')
            row = connection.execute('SELECT * FROM listings WHERE id = ?', (listing_id,)).fetchone()
            if not row or visible_only and not row['matches_criteria'] and not connection.execute(
                    'SELECT 1 FROM contacts WHERE listing_id = ? UNION SELECT 1 FROM viewings WHERE listing_id = ?', (listing_id, listing_id)).fetchone():
                raise KeyError('Listing not found.')
            listing = json.loads(row['data'])
            listing['matches_criteria'] = bool(row['matches_criteria'])
            ranking = connection.execute('SELECT * FROM ranking WHERE listing_id = ?', (listing_id,)).fetchone()
            listing.update(rank=ranking['rank'] if ranking else None, reason=ranking['reason'] if ranking else None,
                           explanation=json.loads(ranking['explanation']) if ranking and ranking['explanation'] else None,
                           exploration=bool(ranking and ranking['exploration']),
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
            explain_pending_rankings(connection, [listing])
            if listing['rank'] is None:
                listing['explanation'] = None
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
                connection.execute('UPDATE notes_session SET session_id = ? WHERE id = 1', (session_id,))

    def chat_state(self, page=1):
        validate_page(page, 20)
        with self.connect() as connection:
            rows = connection.execute('SELECT * FROM chat_messages ORDER BY id DESC LIMIT 20 OFFSET ?', ((page - 1) * 20,))
            messages = [{**dict(row), 'events': json.loads(row['events']), 'prompt': json.loads(row['prompt']) if row['prompt'] else None} for row in rows]
            return {'messages': list(reversed(messages)), 'total': connection.execute('SELECT COUNT(*) FROM chat_messages').fetchone()[0],
                    'active': bool(connection.execute("SELECT 1 FROM chat_messages WHERE status = 'running'").fetchone()),
                    'session_id': connection.execute('SELECT session_id FROM notes_session WHERE id = 1').fetchone()[0]}

    def browse(self, view='Rankings', *, page=1, page_size=24):
        with self.connect() as connection:
            result = browse(connection, view, page, page_size)
            explain_pending_rankings(connection, result['items'])
            return result

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
            cursor = connection.execute('''INSERT INTO correspondence
                (listing_id, direction, channel, text, created_at, external_message_id, external_thread_id) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(listing_id, channel, external_message_id) DO NOTHING''',
                (listing_id, direction, channel, text, now, external_message_id, external_thread_id))
            if cursor.rowcount and direction == 'outgoing':
                connection.execute('UPDATE contacts SET handled_through = ? WHERE listing_id = ?', (cursor.lastrowid, listing_id))
                self.handle_answers(connection, listing_id)
            return bool(cursor.rowcount)

    @staticmethod
    def handle_answers(connection, listing_id):
        connection.execute('UPDATE questions SET handled = 1 WHERE listing_id = ? AND answered_at IS NOT NULL', (listing_id,))

    def has_message(self, listing_id, channel, external_message_id):
        with self.connect() as connection:
            return connection.execute('SELECT 1 FROM correspondence WHERE listing_id = ? AND channel = ? AND external_message_id = ?',
                                      (listing_id, channel, external_message_id)).fetchone() is not None

    def mark_handled(self, listing_id):
        with self.connect(write=True) as connection:
            if not connection.execute('''UPDATE contacts SET handled_through = (SELECT COALESCE(MAX(id), 0) FROM correspondence
                WHERE listing_id = ?) WHERE listing_id = ?''', (listing_id, listing_id)).rowcount:
                raise KeyError('Contact request not found.')
            self.handle_answers(connection, listing_id)

    def due_contacts(self):
        with self.connect() as connection:
            return [{'listing_id': row['listing_id'], 'reason': 'reply' if row['reply'] else 'answer' if row['answer'] else 'start'}
                    for row in connection.execute('''SELECT * FROM (SELECT c.listing_id, c.status, c.requested_at,
                    EXISTS (SELECT 1 FROM correspondence m WHERE m.listing_id = c.listing_id AND m.direction = 'incoming'
                            AND m.id > c.handled_through) AS reply,
                    EXISTS (SELECT 1 FROM questions q WHERE q.listing_id = c.listing_id AND q.answered_at IS NOT NULL AND q.handled = 0) AS answer,
                    NOT EXISTS (SELECT 1 FROM correspondence m WHERE m.listing_id = c.listing_id AND m.direction = 'outgoing') AS unsent,
                    EXISTS (SELECT 1 FROM questions q WHERE q.listing_id = c.listing_id AND q.answered_at IS NULL) AS waiting
                FROM contacts c WHERE c.status IN ('pending', 'in_progress', 'contacted'))
                WHERE NOT waiting AND (reply OR answer OR (unsent AND status IN ('pending', 'in_progress')))
                ORDER BY requested_at, listing_id''')]

    def thread_owners(self, channel):
        """Maps each thread of an open contact to the listing that last wrote in it, so a shared agency address or
        number files a reply under the conversation it continues."""
        with self.connect() as connection:
            return {row['external_thread_id']: row['listing_id'] for row in connection.execute('''SELECT m.listing_id, m.external_thread_id,
                MAX(m.id) AS last FROM correspondence m JOIN contacts c ON c.listing_id = m.listing_id
                WHERE m.channel = ? AND m.direction = 'outgoing' AND m.external_thread_id IS NOT NULL
                AND c.status IN ('pending', 'in_progress', 'contacted') GROUP BY m.listing_id, m.external_thread_id ORDER BY last''', (channel,))}

    def active_contacts(self):
        with self.connect() as connection:
            return [{**dict(row), 'agent': json.loads(row['agent'])} for row in connection.execute(
                "SELECT * FROM contacts WHERE status IN ('pending', 'in_progress', 'contacted') ORDER BY listing_id")]

    def ask_user(self, listing_id, question, *, field=None, purpose=None, external_message_id=None):
        question = require_text(question, 'Question', limit=4000)
        field = None if field is None else require_text(field, 'Field', limit=200)
        purpose = None if purpose is None else require_text(purpose, 'Purpose', limit=1000)
        with self.connect(write=True) as connection:
            if not connection.execute('SELECT 1 FROM contacts WHERE listing_id = ?', (listing_id,)).fetchone():
                raise KeyError('Contact request not found.')
            connection.execute("UPDATE contacts SET status = 'in_progress' WHERE listing_id = ? AND status = 'pending'", (listing_id,))
            return connection.execute('''INSERT INTO questions (listing_id, question, field, purpose, asked_at, external_message_id)
                VALUES (?, ?, ?, ?, ?, ?)''', (listing_id, question, field, purpose, datetime.now(timezone.utc).isoformat(),
                                               external_message_id)).lastrowid

    def unhandled_answers(self, listing_id):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                'SELECT * FROM questions WHERE listing_id = ? AND answered_at IS NOT NULL AND handled = 0 ORDER BY id', (listing_id,))]

    def open_questions(self, listing_id=None):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                'SELECT * FROM questions WHERE answered_at IS NULL AND (? IS NULL OR listing_id = ?) ORDER BY id', (listing_id, listing_id))]

    def answer_question(self, question_id, answer, *, share=None):
        answer = require_text(answer, 'Answer')
        if share is not None:
            share = require_text(share, 'Shared value')
        with self.connect(write=True) as connection:
            question = connection.execute('SELECT * FROM questions WHERE id = ?', (question_id,)).fetchone()
            if not question:
                raise KeyError('Question not found.')
            if question['answered_at']:
                raise ValueError('This question is already answered.')
            if share is not None and not (question['field'] and question['purpose']):
                raise ValueError('Only a question that names a field and purpose can approve sharing a value.')
            now = datetime.now(timezone.utc).isoformat()
            connection.execute('UPDATE questions SET answer = ?, answered_at = ? WHERE id = ?', (answer, now, question_id))
            if share is not None:
                connection.execute('INSERT INTO approvals (listing_id, field, value, purpose, approved_at) VALUES (?, ?, ?, ?, ?)',
                                   (question['listing_id'], question['field'], share, question['purpose'], now))

    def set_contact(self, listing_id, status, *, channel=None, agent=None, error=None, subject=None):
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
            current = connection.execute('SELECT agent FROM contacts WHERE listing_id = ?', (listing_id,)).fetchone()
            if not current:
                raise KeyError('Contact request not found.')
            connection.execute('''UPDATE contacts SET status = ?, channel = COALESCE(?, channel), agent = ?, error = ?,
                subject = COALESCE(?, subject) WHERE listing_id = ?''',
                               (status, channel, json.dumps(agent) if agent is not None else current['agent'], error, subject, listing_id))

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

    def approvals(self, listing_id):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM approvals WHERE listing_id = ? AND revoked_at IS NULL ORDER BY id',
                                                            (listing_id,))]

    def revoke_approval(self, approval_id):
        with self.connect(write=True) as connection:
            if not connection.execute('UPDATE approvals SET revoked_at = ? WHERE id = ?',
                                      (datetime.now(timezone.utc).isoformat(), approval_id)).rowcount:
                raise KeyError('Approval not found.')

    def agree_viewing(self, listing_id, time, details, *, viewing_id=None):
        time = viewing_time(time)
        if not isinstance(details, str):
            raise ValueError('Viewing details must be text.')
        start = datetime.fromisoformat(time)
        with self.connect(write=True) as connection:
            self.require_listing(connection, listing_id)
            for other in connection.execute("SELECT * FROM viewings WHERE status IN ('agreed', 'booked') AND id IS NOT ?", (viewing_id,)):
                if abs(datetime.fromisoformat(other['time']) - start) < timedelta(minutes=VIEWING_MINUTES):
                    raise ViewingConflict(f'The viewing of {other["listing_id"]} at {other["time"]} overlaps {time}.')
            if viewing_id:
                if not connection.execute('''UPDATE viewings SET time = ?, details = ?, status = 'agreed'
                    WHERE id = ? AND listing_id = ? AND status IN ('agreed', 'booked')''', (time, details, viewing_id, listing_id)).rowcount:
                    raise KeyError('No agreed or booked viewing with that id for this listing.')
                return viewing_id
            viewing_id = str(uuid4())
            connection.execute("INSERT INTO viewings (id, listing_id, time, details, status) VALUES (?, ?, ?, ?, 'agreed')",
                               (viewing_id, listing_id, time, details))
            return viewing_id

    def agreed_viewings(self):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM viewings WHERE status = 'agreed' ORDER BY time, id")]

    def scheduled_viewings(self):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM viewings WHERE status IN ('agreed', 'booked') ORDER BY time, id")]

    def canceled_viewing_events(self):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM viewings WHERE status = 'canceled' AND calendar_event_id IS NOT NULL ORDER BY time, id")]

    def record_booking(self, viewing_id, time, details, calendar_event_id):
        """Books only a viewing still agreed with the time and details the event was written with, so a move that arrives
        during the calendar write stays agreed for the next write. A viewing canceled meanwhile takes the identifier of
        the event just written, so that event is removed instead of left behind."""
        require_text(calendar_event_id, 'Calendar event id', limit=1000)
        with self.connect(write=True) as connection:
            if connection.execute('''UPDATE viewings SET status = 'booked', calendar_event_id = ?
                WHERE id = ? AND status = 'agreed' AND time = ? AND details = ?''', (calendar_event_id, viewing_id, time, details)).rowcount:
                return True
            connection.execute("UPDATE viewings SET calendar_event_id = ? WHERE id = ? AND status = 'canceled'", (calendar_event_id, viewing_id))
            return False

    def record_event_removed(self, viewing_id, calendar_event_id):
        with self.connect(write=True) as connection:
            return bool(connection.execute('''UPDATE viewings SET calendar_event_id = NULL
                WHERE id = ? AND status = 'canceled' AND calendar_event_id = ?''', (viewing_id, calendar_event_id)).rowcount)

    def cancel_viewing(self, viewing_id, *, possible_event_id):
        """A viewing without a recorded event may still have one, written by a create whose answer was lost, so it keeps
        `possible_event_id` for the removal, which counts an event that does not exist as removed."""
        require_text(possible_event_id, 'Calendar event id', limit=1000)
        with self.connect(write=True) as connection:
            if not connection.execute('''UPDATE viewings SET status = 'canceled', calendar_event_id = CASE WHEN status = 'canceled'
                THEN calendar_event_id ELSE COALESCE(calendar_event_id, ?) END WHERE id = ?''', (possible_event_id, viewing_id)).rowcount:
                raise KeyError('Viewing not found.')

    @staticmethod
    def require_listing(connection, listing_id):
        if not connection.execute('SELECT 1 FROM listings WHERE id = ?', (listing_id,)).fetchone():
            raise KeyError('Listing not found.')
