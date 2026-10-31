"""The part of a listing's feature record that code reads reliably, without a model call.

Computed from the stored listing and the facts its listing site gave (see matching.py for the facts), and
stored in the code_features table for the content it was computed from:

  monthly_price   the cheapest unit's price as a monthly amount, across rental periods; a period of
                  'total' has no monthly amount
  currency        three-letter code of monthly_price
  size_sqft       that unit's size in square feet
  price_per_sqft  monthly_price over size_sqft, unknown when either is
  bedrooms, bathrooms
  unit_label      that unit's label, such as "Unit G2", or the unit part of the listing's address, such as "G"
  unit_floor      the unit's floor, from the facts or the label
  unit_level      garden (a garden or basement unit), ground, middle or top; unknown when neither the label
                  nor the facts settle it. A label that names the level outranks extraction's reading from
                  photos and text, so a consumer takes this level when it is known and extraction's otherwise.
  photo_count     photos stored with the listing
  latitude, longitude
  nearest_station, station_distance_km
                  the name of the rapid-transit station nearest the listing and the great-circle distance to it
  place_distances_km
                  the great-circle distance to each of the user's places that has coordinates, by place name

When no unit has a price, the features describe the only unit, or none when there are several. Any value
the listing does not state is null. A listing without coordinates has no distances, and neither does one when the
store has no stations or no geocoded places. Days since first seen depends on the day it is counted on, so
ListingStore.code_features counts it when read.
"""

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Literal, TypedDict, cast

from shapes import Listing, Place, RentalPeriod

PERIODS_PER_YEAR = {'day': 365, 'week': 52, 'month': 12, 'year': 1}
FEATURES = ('monthly_price', 'currency', 'size_sqft', 'price_per_sqft', 'bedrooms', 'bathrooms', 'unit_label', 'unit_floor',
            'unit_level', 'photo_count', 'latitude', 'longitude', 'nearest_station', 'station_distance_km', 'place_distances_km')
EARTH_RADIUS_KM = 6371.0088
DEFAULT_TRANSIT_FEED = 'https://www.transitchicago.com/downloads/sch_data/google_transit.zip'

UNIT_WORD = re.compile(r'(?:#|(?:apt|unit|ste|suite)\b\.?)\s*#?\s*', re.I)
ADDRESS_UNIT = re.compile(r'(?:#|\b(?:apt|unit|ste|suite)\b\.?)\s*#?\s*([^,#]+?)\s*(?:,|$)', re.I)
GARDEN = re.compile(r'(?:g|gdn|grdn|garden|bsmt|basement|ll|lower(?: level)?)(?:[ -]?(?:\d{1,2}|[nsewfr]|front|rear))?'
                    + r'(?: (?:unit|level|apt|apartment))?', re.I)
PENTHOUSE = re.compile(r'ph[ -]?\d{0,2}|penthouse(?: \w+)?', re.I)
FLOOR = (re.compile(r'([1-9])[a-z]{1,2}(?: floor| fl)?', re.I), re.compile(r'([1-9])\d{2}'),
         re.compile(r'([1-9]\d?)(?:st|nd|rd|th) (?:floor|fl)', re.I))

Level = Literal['garden', 'ground', 'middle', 'top']
Coordinates = tuple[float, float]
Landmark = tuple[str, float, float]


class UnitFacts(TypedDict, total=False):
    """One rentable unit as the listing site states it, any value null."""
    price: int | float | None
    period: RentalPeriod | None
    bedrooms: int | float | None
    bathrooms: int | float | None
    size_sqft: int | float | None
    available_on: str | None
    label: str | None
    floor: int | None


class Facts(TypedDict, total=False):
    """What the search wrote from the listing site's structured data (matching.py)."""
    areas: list[str]
    currency: str | None
    latitude: float | None
    longitude: float | None
    stories: int | None
    units: list[UnitFacts]


class RankingFeatures(TypedDict, total=False):
    """The ranking table's features column: the site facts and the model's must-have judgments by phrase."""
    facts: Facts
    must_haves: dict[str, bool | None]


class Distances(TypedDict):
    nearest_station: str | None
    station_distance_km: float | None
    place_distances_km: dict[str, float]


class CodeFeatures(Distances):
    """A listing's code features as the module docstring describes them."""
    monthly_price: float | None
    currency: str | None
    size_sqft: int | float | None
    price_per_sqft: float | None
    bedrooms: int | float | None
    bathrooms: int | float | None
    unit_label: str | None
    unit_floor: int | None
    unit_level: Level | None
    photo_count: int
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class Surroundings:
    """What a listing's distances are measured to: the rapid-transit stations as (name, latitude, longitude) and the
    user's geocoded places as (name, latitude, longitude)."""
    stations: tuple[Landmark, ...] = ()
    places: tuple[Landmark, ...] = ()


def distance_km(first: Coordinates, second: Coordinates) -> float:
    """The great-circle distance between two (latitude, longitude) points in degrees, by the haversine formula on the
    mean Earth radius."""
    latitude_1, longitude_1, latitude_2, longitude_2 = map(math.radians, (*first, *second))
    haversine = (math.sin((latitude_2 - latitude_1) / 2) ** 2
                 + math.cos(latitude_1) * math.cos(latitude_2) * math.sin((longitude_2 - longitude_1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(haversine)))


def distances(latitude: float | None, longitude: float | None, surroundings: Surroundings) -> Distances:
    """The listing's nearest station, its distance and the distance to each place, unknown without coordinates."""
    if latitude is None or longitude is None:
        return {'nearest_station': None, 'station_distance_km': None, 'place_distances_km': {}}
    here = (latitude, longitude)
    nearest = min(((distance_km(here, (station_latitude, station_longitude)), name) for name, station_latitude, station_longitude
                   in surroundings.stations), default=(None, None))
    return {'nearest_station': nearest[1], 'station_distance_km': nearest[0],
            'place_distances_km': {name: distance_km(here, (place_latitude, place_longitude))
                                   for name, place_latitude, place_longitude in surroundings.places}}


def monthly(price: int | float | None, period: str | None) -> float | None:
    return price * PERIODS_PER_YEAR[period] / 12 if price is not None and period in PERIODS_PER_YEAR else None


def bare_label(label: str | None) -> str:
    return UNIT_WORD.sub('', label.strip(), count=1).strip() if label else ''


def label_floor(label: str) -> int | None:
    return next((int(match.group(1)) for pattern in FLOOR if (match := pattern.fullmatch(label))), None)


def unit_level(label: str | None, floor: int | None, stories: int | None) -> tuple[int | None, Level | None]:
    """(floor, level) of a unit from its label and the floor and building height its facts state, any of them None.
    Floors count from 1 at street level, as RESO's EntryLevel and StoriesTotal do."""
    label = bare_label(label)
    if GARDEN.fullmatch(label):
        return None, 'garden'
    if PENTHOUSE.fullmatch(label):
        return None, 'top'
    floor = floor or label_floor(label)
    if floor == 1:
        return 1, 'ground'
    if floor and stories and floor <= stories:
        return floor, 'top' if floor == stories else 'middle'
    return floor, None


def address_label(listing: Listing) -> str | None:
    match = ADDRESS_UNIT.search(listing.get('address') or listing.get('title') or '')
    return match.group(1) if match else None


def chosen_unit(units: list[UnitFacts]) -> UnitFacts:
    priced = [(amount, unit) for unit in units if (amount := monthly(unit.get('price'), unit.get('period'))) is not None]
    if priced:
        return min(priced, key=lambda pair: pair[0])[1]
    return units[0] if len(units) == 1 else {}


def code_features(listing: Listing, facts: Facts | None, surroundings: Surroundings | None = None) -> CodeFeatures:
    """The features of a listing, a stored listing object, given its site facts or None and its surroundings."""
    facts = facts or {}
    units = facts.get('units') or [UnitFacts(price=listing.get('price'), period=listing.get('rental_period'))]
    unit = chosen_unit(units)
    price = monthly(unit.get('price'), unit.get('period'))
    size = unit.get('size_sqft') or None
    label = unit.get('label') or address_label(listing)
    floor, level = unit_level(label, unit.get('floor'), facts.get('stories'))
    return {
        'monthly_price': price, 'currency': facts.get('currency') or listing.get('currency'), 'size_sqft': size,
        'price_per_sqft': price / size if price is not None and size else None,
        'bedrooms': unit.get('bedrooms'), 'bathrooms': unit.get('bathrooms'),
        'unit_label': label, 'unit_floor': floor, 'unit_level': level, 'photo_count': len(listing.get('photos') or []),
        'latitude': facts.get('latitude'), 'longitude': facts.get('longitude'),
        **distances(facts.get('latitude'), facts.get('longitude'), surroundings or Surroundings()),
    }


def configured_transit_feed(connection: sqlite3.Connection) -> str:
    """The transit feed URL the settings name, or the default."""
    row = cast(tuple[str] | None, connection.execute("SELECT value FROM settings WHERE name = 'transit_feed'").fetchone())
    return cast(str, json.loads(row[0])) if row else DEFAULT_TRANSIT_FEED


def fetched_stations(connection: sqlite3.Connection) -> tuple[Landmark, ...]:
    """The stored stations as (name, latitude, longitude), when they were fetched from the configured feed."""
    fetched = cast(tuple[str] | None, connection.execute('SELECT url FROM transit_feed').fetchone())
    if not fetched or fetched[0] != configured_transit_feed(connection):
        return ()
    rows = cast(list[Landmark], connection.execute('SELECT name, latitude, longitude FROM stations ORDER BY id').fetchall())
    return tuple((name, latitude, longitude) for name, latitude, longitude in rows)


def stored_surroundings(connection: sqlite3.Connection) -> Surroundings:
    """The stations of the configured transit feed, when they have been fetched, and the places with coordinates."""
    row = cast(tuple[str] | None, connection.execute("SELECT value FROM settings WHERE name = 'places'").fetchone())
    places: list[Landmark] = []
    for place in cast(list[Place], json.loads(row[0])) if row else []:
        found = cast(tuple[float, float] | None,
                     connection.execute('SELECT latitude, longitude FROM geocodes WHERE address = ?', (place['address'],)).fetchone())
        if found:
            places.append((place['name'], found[0], found[1]))
    return Surroundings(fetched_stations(connection), tuple(places))


def store_all_code_features(connection: sqlite3.Connection) -> None:
    """Computes and stores the features of every listing, as when their surroundings change."""
    surroundings = stored_surroundings(connection)
    for (listing_id,) in cast(list[tuple[str]], connection.execute('SELECT id FROM listings').fetchall()):
        store_code_features(connection, listing_id, surroundings)


def store_code_features(connection: sqlite3.Connection, listing_id: str, surroundings: Surroundings | None = None) -> None:
    """Computes and stores the features of a listing's current content, from its facts when they are for that content."""
    row = cast(tuple[str, str, str | None], connection.execute('''SELECT l.data, l.fingerprint, CASE WHEN r.fingerprint = l.fingerprint THEN r.features END AS features
        FROM listings l LEFT JOIN ranking r ON r.listing_id = l.id WHERE l.id = ?''', (listing_id,)).fetchone())
    facts = cast(RankingFeatures, json.loads(row[2])).get('facts') if row[2] else None
    features = code_features(cast(Listing, json.loads(row[0])), facts, stored_surroundings(connection) if surroundings is None else surroundings)
    columns: dict[str, object] = {**features, 'place_distances_km': json.dumps(features['place_distances_km'], ensure_ascii=False)}
    _ = connection.execute(f'''INSERT INTO code_features (listing_id, fingerprint, {", ".join(FEATURES)})
        VALUES (?, ?, {", ".join("?" for _ in FEATURES)}) ON CONFLICT(listing_id) DO UPDATE SET fingerprint = excluded.fingerprint,
        {", ".join(f"{name} = excluded.{name}" for name in FEATURES)}''', (listing_id, row[1], *(columns[name] for name in FEATURES)))
