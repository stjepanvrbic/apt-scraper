"""Where the user's places and the city's rapid-transit stations are, from two public sources.

Places are geocoded with the public Nominatim server of OpenStreetMap, under its usage policy
(https://operations.osmfoundation.org/policies/nominatim/): at most one request per second, a User-Agent that names
this application, results cached on our side, no bulk or periodic queries and no autocomplete. A place is geocoded
once, when the user saves it, and the store keeps the result by address. Results are OpenStreetMap data, so wherever
the user sees them they carry ATTRIBUTION.

Stations come from the city's GTFS schedule feed (https://gtfs.org/documentation/schedule/reference/): the stops that
a rail, metro or light rail route serves, each counted at its parent station when it has one. GTFS has no route type
for bus rapid transit, so a feed's bus routes never count. The feed is downloaded once and only the stations are kept.
"""

import csv
import io
import json
import math
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager
from typing import Protocol, TypedDict, cast

from shapes import is_array, is_object, loads

NOMINATIM = 'https://nominatim.openstreetmap.org/search'
USER_AGENT = 'apt-scraper (Hermes Agent apartment search)'
ATTRIBUTION = '© OpenStreetMap contributors'
NOMINATIM_INTERVAL_SECONDS = 1.0
TIMEOUT_SECONDS = 60
BASIC_RAIL_TYPES = {0, 1, 2, 12}
EXTENDED_RAIL_TYPES = (range(100, 200), range(400, 500), range(900, 1000))


class SourceUnavailable(Exception):
    """A source did not answer, or answered with something other than what its documentation describes."""


class Readable(Protocol):
    """The body of an HTTP answer, as urlopen's response reads it."""
    def read(self, size: int = ..., /) -> bytes: ...


Response = AbstractContextManager[Readable]
Opener = Callable[[str], Response]


class Location(TypedDict):
    latitude: float
    longitude: float
    found: str


class Station(TypedDict):
    id: str
    name: str
    latitude: float
    longitude: float


def open_url(url: str) -> Response:
    return cast(Response, urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=TIMEOUT_SECONDS))


def as_float(value: object) -> float:
    if not isinstance(value, (str, int, float)):
        raise TypeError(f'{value!r} is not a number.')
    return float(value)


def found_place(answer: object) -> Location | None:
    """The first result of a Nominatim search answer, a JSON array: {latitude, longitude, found}, where found is the
    result's display name, or None when the array is empty."""
    if not is_array(answer):
        raise SourceUnavailable('Nominatim answered with something other than a list of results.')
    if not answer:
        return None
    result = answer[0]
    try:
        if not is_object(result):
            raise TypeError('The result is not an object.')
        latitude, longitude, found = as_float(result['lat']), as_float(result['lon']), result['display_name']
    except (TypeError, KeyError, ValueError):
        raise SourceUnavailable('Nominatim answered with a result without coordinates or a name.') from None
    if not (math.isfinite(latitude) and math.isfinite(longitude) and abs(latitude) <= 90 and abs(longitude) <= 180 and isinstance(found, str)):
        raise SourceUnavailable('Nominatim answered with coordinates outside the globe.')
    return {'latitude': latitude, 'longitude': longitude, 'found': found}


def search_place(address: str, opener: Opener = open_url) -> Location | None:
    """The location Nominatim finds first for the address, or None when it finds none."""
    url = f'{NOMINATIM}?{urllib.parse.urlencode({"q": address, "format": "jsonv2", "limit": 1})}'
    try:
        with opener(url) as response:
            return found_place(loads(response.read().decode('utf-8')))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SourceUnavailable(f'Nominatim could not be reached: {error}') from None


def geocode(addresses: Iterable[str], search: Callable[[str], Location | None] = search_place,
            sleep: Callable[[float], object] = time.sleep) -> tuple[dict[str, Location], list[str], SourceUnavailable | None]:
    """The locations found for the addresses, by address, the addresses not found, and the SourceUnavailable that
    stopped the lookups early or None, one request at a time with NOMINATIM_INTERVAL_SECONDS between them."""
    found: dict[str, Location] = {}
    missing: list[str] = []
    for index, address in enumerate(addresses):
        if index:
            _ = sleep(NOMINATIM_INTERVAL_SECONDS)
        try:
            location = search(address)
        except SourceUnavailable as error:
            return found, missing, error
        if location is None:
            missing.append(address)
        else:
            found[address] = location
    return found, missing, None


def rail_route_type(value: str) -> bool:
    """Whether a GTFS route_type is rail, metro, light rail or monorail, among the basic types or the extended ones
    (railway, urban railway and tram services)."""
    try:
        route_type = int(value)
    except ValueError:
        return False
    return route_type in BASIC_RAIL_TYPES or any(route_type in types for types in EXTENDED_RAIL_TYPES)


def coordinate(value: str, limit: float) -> float:
    number = float(value)
    if not math.isfinite(number) or abs(number) > limit:
        raise ValueError(f'{value} is not a coordinate.')
    return number


def rows(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    with archive.open(name) as file:
        yield from csv.DictReader(io.TextIOWrapper(file, encoding='utf-8-sig', newline=''))


def rapid_transit_stations(archive: zipfile.ZipFile) -> list[Station]:
    """The rapid-transit stations of a GTFS feed, an open zipfile.ZipFile: each stop a rail route serves, as its parent
    station when it has one, {id, name, latitude, longitude}, sorted by id."""
    routes = {row['route_id'] for row in rows(archive, 'routes.txt') if rail_route_type(row['route_type'])}
    trips = {row['trip_id'] for row in rows(archive, 'trips.txt') if row['route_id'] in routes}
    with archive.open('stop_times.txt') as file:
        reader = csv.reader(io.TextIOWrapper(file, encoding='utf-8-sig', newline=''))
        header = next(reader, None)
        if header is None:
            raise ValueError('stop_times.txt is empty.')
        trip_column, stop_column = header.index('trip_id'), header.index('stop_id')
        width = max(trip_column, stop_column) + 1
        served = {row[stop_column] for row in reader if len(row) >= width and row[trip_column] in trips}
    stops = {row['stop_id']: row for row in rows(archive, 'stops.txt')}
    stations: dict[str, Station] = {}
    for stop_id in served:
        stop = stops[stop_id]
        station = stops.get(stop.get('parent_station') or '', stop)
        stations[station['stop_id']] = {'id': station['stop_id'], 'name': station['stop_name'],
                                        'latitude': coordinate(station['stop_lat'], 90), 'longitude': coordinate(station['stop_lon'], 180)}
    return sorted(stations.values(), key=lambda station: station['id'])


def fetch_stations(url: str, opener: Opener = open_url) -> list[Station]:
    """The rapid-transit stations of the GTFS feed at url, downloaded to a temporary file."""
    with tempfile.TemporaryFile() as file:
        try:
            with opener(url) as response:
                shutil.copyfileobj(response, file)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SourceUnavailable(f'The transit feed {url} could not be downloaded: {error}') from None
        try:
            with zipfile.ZipFile(file) as archive:
                return rapid_transit_stations(archive)
        except Exception as error:
            raise SourceUnavailable(f'The transit feed {url} is not a GTFS feed with routes, trips, stop times and stops: {error}') from None
