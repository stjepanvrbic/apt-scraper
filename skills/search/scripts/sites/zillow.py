"""Zillow rentals, read from the JSON its pages embed for their own rendering (__NEXT_DATA__).

Search pages list cards in Zillow's default order, "priorityscore". A query shows at most 20 pages of 41
cards, so a query with more results is split into price bands. A card is a building with several units
(id zillow:building-<lot id>) or a single unit (id zillow:<zpid>). Units in a building are read from
the building page, homes from their home page.
"""

import hashlib
import json
import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NotRequired, TypedDict, TypeGuard, cast
from urllib.parse import quote, urljoin

from client import Fetcher
from matching import Facts, Unit
from shapes import Agent, Criteria, RentalPeriod, Source, is_array, is_object, loads
from sites.errors import Blocked, Changed, Gone

NAME = 'Zillow'
HOSTS = {'zillow.com', 'www.zillow.com'}
ORIGIN = 'https://www.zillow.com'
AUTOCOMPLETE = 'https://www.zillowstatic.com/autocomplete/v3/suggestions?q='
REGION_TYPES = {'state': 2, 'county': 4, 'city': 6, 'zipcode': 7, 'neighborhood': 8}
PERIODS_PER_YEAR = {'day': 365, 'week': 52, 'month': 12, 'year': 1}
SQUARE_FEET_PER_SQUARE_METER = 10.7639


class Flag(TypedDict):
    value: bool


class Sort(TypedDict):
    value: str


class RentalsOnly(TypedDict):
    fr: Flag
    fsba: Flag
    fsbo: Flag
    nc: Flag
    cmsn: Flag
    auc: Flag
    fore: Flag


RENTALS_ONLY: RentalsOnly = {'fr': {'value': True}, 'fsba': {'value': False}, 'fsbo': {'value': False}, 'nc': {'value': False},
                             'cmsn': {'value': False}, 'auc': {'value': False}, 'fore': {'value': False}}
DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
MAX_PHOTOS = 40
MAX_UNIT_LINES = 40
# Part of every card's signature, so a new reader reads each stored listing once more and adds the facts it reads.
READER = 2


class Region(TypedDict):
    regionId: int
    regionType: int


class Limits(TypedDict):
    min: int | None
    max: int | None


class Filters(RentalsOnly):
    sort: Sort
    mp: NotRequired[Limits]
    beds: NotRequired[Limits]
    sqft: NotRequired[Limits]


class Search(TypedDict):
    area: str
    region: Region
    filters: Filters


class QueryState(TypedDict):
    pagination: dict[str, int]
    isMapVisible: bool
    isListVisible: bool
    regionSelection: list[Region]
    filterState: Filters


Band = tuple[int | None, int | None]


@dataclass
class Remaining:
    """A price band whose later pages are still to be read."""
    band: Band
    number: int
    last: int


class ListingPage(TypedDict):
    """What the reader needs to read a listing's page: where it is and, for one unit of a building, which unit."""
    id: str
    url: str
    building: bool
    zpid: str | None
    available_on: str | None


class Card(ListingPage):
    """A listing page as a results page showed it, with a signature of what the card showed."""
    signature: str


class Offer(TypedDict):
    zpid: str
    plan: str
    label: str | None
    unit: str | None
    price: int | float | None
    beds: int | float | None
    baths: int | float | None
    sqft: int | float | None
    floor: int | None
    available_on: str | None
    photos: list[object]


class Coordinates(TypedDict):
    latitude: float | None
    longitude: float | None


class SiteListing(TypedDict):
    """A listing as the site states it; the store fills in what validate_listing defaults."""
    id: str
    title: str | None
    address: str | None
    price: int | float | None
    currency: str
    rental_period: RentalPeriod
    description: str | None
    amenities: list[str]
    rooms: str | None
    size: str | None
    photos: list[str]
    sources: list[Source]
    agent: Agent


class SourcedListing(TypedDict):
    """What reread needs of a stored listing: its id and the source link."""
    id: str
    sources: list[Source]


def obj(value: object) -> dict[str, object]:
    """A JSON object of the page, or an empty one where the page has null or nothing."""
    if is_object(value):
        return value
    if not value:
        return {}
    raise TypeError(f'expected an object, got {type(value).__name__}')


def arr(value: object) -> list[object]:
    """A JSON array of the page, or an empty one where the page has null or nothing."""
    if is_array(value):
        return value
    if not value:
        return []
    raise TypeError(f'expected an array, got {type(value).__name__}')


def text(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f'expected text, got {type(value).__name__}')


def texts(value: object) -> list[str]:
    found: list[str] = []
    for item in arr(value):
        if not isinstance(item, str):
            raise TypeError(f'expected text, got {type(item).__name__}')
        found.append(item)
    return found


def num(value: object) -> int | float | None:
    if value is None or isinstance(value, (int, float)):
        return value
    raise TypeError(f'expected a number, got {type(value).__name__}')


def integer(value: object) -> int:
    if isinstance(value, (int, float, str)):
        return int(value)
    raise TypeError(f'expected a number, got {type(value).__name__}')


def member(value: object, *keys: str) -> object:
    """The value under the keys, in turn; KeyError for a key the page lacks and TypeError where it has no object."""
    for key in keys:
        if not is_object(value):
            raise TypeError(f'{key} read from {type(value).__name__}')
        value = value[key]
    return value


def fetch(client: Fetcher, url: str) -> str:
    status, body = client.get(url)
    if status in (403, 429) or 'px-captcha' in body:
        raise Blocked(f'Zillow refused {url} with a bot check (HTTP {status}).')
    if status in (404, 410):
        raise Gone(url)
    if status != 200:
        raise ConnectionError(f'Zillow answered HTTP {status} for {url}.')
    return body


def page_data(text: str, url: str) -> object:
    match = DATA.search(text)
    if not match:
        raise Changed(f'{url} has no page data.')
    try:
        return loads(match.group(1))
    except json.JSONDecodeError as error:
        raise Changed(f'{url} has page data that is not JSON: {error}') from None


def resolve(area: str, client: Fetcher) -> Region:
    results = arr(obj(loads(fetch(client, AUTOCOMPLETE + quote(area)))).get('results'))
    for result in map(obj, results):
        meta = obj(result.get('metaData'))
        region_type = meta.get('regionType')
        if result.get('resultType') == 'Region' and isinstance(region_type, str) and region_type in REGION_TYPES:
            region_id = meta['regionId']
            if not isinstance(region_id, int):
                raise Changed(f'Zillow names region {area} with {region_id!r}, not a number.')
            return {'regionId': region_id, 'regionType': REGION_TYPES[region_type]}
    raise LookupError(f'Zillow knows no area called "{area}".')


def limits(low: int | float | None, high: int | float | None, scale: float = 1) -> Limits:
    return {'min': None if low is None else math.floor(low * scale), 'max': None if high is None else math.ceil(high * scale)}


def filters(criteria: Criteria) -> Filters:
    state: Filters = {**RENTALS_ONLY, 'sort': {'value': 'priorityscore'}}
    price = criteria.get('price', {})
    period = price.get('period')
    if price.get('currency') == 'USD' and period in PERIODS_PER_YEAR and (price.get('min'), price.get('max')) != (None, None):
        state['mp'] = limits(price.get('min'), price.get('max'), PERIODS_PER_YEAR[period] / 12)
    rooms = criteria.get('rooms', {})
    if (rooms.get('min'), rooms.get('max')) != (None, None):
        state['beds'] = limits(rooms.get('min'), rooms.get('max'))
    size = criteria.get('size', {})
    if (size.get('min'), size.get('max')) != (None, None):
        state['sqft'] = limits(size.get('min'), size.get('max'), SQUARE_FEET_PER_SQUARE_METER if size.get('unit') == 'm2' else 1)
    return state


def searches(criteria: Criteria, client: Fetcher, regions: dict[str, object]) -> list[Search]:
    found: list[Search] = []
    for area in criteria.get('location', []):
        if area not in regions:
            regions[area] = resolve(area, client)
        found.append({'area': area, 'region': cast(Region, regions[area]), 'filters': filters(criteria)})
    return found


def search_url(search: Search, band: Band, number: int) -> str:
    state: QueryState = {'pagination': {'currentPage': number} if number > 1 else {}, 'isMapVisible': False, 'isListVisible': True,
                         'regionSelection': [search['region']], 'filterState': {**search['filters'], 'mp': {'min': band[0], 'max': band[1]}}}
    if band == (None, None):
        del state['filterState']['mp']
    path = '/homes/for_rent/' + (f'{number}_p/' if number > 1 else '')
    return ORIGIN + path + '?searchQueryState=' + quote(json.dumps(state, separators=(',', ':')))


def results_page(client: Fetcher, search: Search, band: Band, number: int) -> tuple[tuple[int, int, int], list[Card]]:
    """((total results, results per page, pages), cards) of one result page."""
    url = search_url(search, band, number)
    try:
        listing = member(page_data(fetch(client, url), url), 'props', 'pageProps', 'searchPageState', 'cat1')
        summary = member(listing, 'searchList')
        counts = (integer(member(summary, 'totalResultCount')), integer(member(summary, 'resultsPerPage')), integer(member(summary, 'totalPages')))
        return counts, [card(obj(raw)) for raw in arr(member(listing, 'searchResults', 'listResults'))]
    except Gone:
        raise Changed(f'Zillow has no search results page at {url}.') from None
    except (KeyError, TypeError, ValueError) as error:
        raise Changed(f'Zillow search results changed shape: {error!r}') from None


def split(band: Band) -> list[Band]:
    low, high = band
    start = low or 0
    middle = (start + high) // 2 if high is not None else max(start * 2, start + 1000)
    return [(low, middle), (middle + 1, high)]


def pages(search: Search, client: Fetcher) -> Iterator[list[Card]]:
    """Zillow places the query's first page highest, then the first page of each price band the query is split
    into, then the later pages of every band in turn."""
    mp = search['filters'].get('mp', {})
    opening: list[Band] = [(mp.get('min'), mp.get('max'))]
    later: list[Remaining] = []
    while opening or later:
        while opening:
            band = opening.pop(0)
            (total, per_page, last), cards = results_page(client, search, band, 1)
            yield cards
            if total > per_page * last and (band[1] is None or band[1] - (band[0] or 0) > 1):
                opening[0:0] = split(band)
            elif last > 1:
                later.append(Remaining(band, 2, last))
        for entry in list(later):
            yield results_page(client, search, entry.band, entry.number)[1]
            entry.number += 1
            if entry.number > entry.last:
                later.remove(entry)


def card(raw: dict[str, object]) -> Card:
    building = bool(raw.get('isBuilding'))
    photos = [obj(photo).get('photoKey') for photo in arr(obj(raw.get('carouselPhotosComposable')).get('photoData'))]
    shown = {key: raw.get(key) for key in ('statusText', 'address', 'price', 'unformattedPrice', 'units', 'beds', 'baths', 'area', 'availabilityDate')}
    return {'id': f'zillow:building-{raw["lotId"]}' if building else f'zillow:{raw["zpid"]}',
            'url': urljoin(ORIGIN, text(raw['detailUrl']) or ''), 'building': building, 'zpid': None if building else str(raw['zpid']),
            'available_on': day(raw.get('availabilityDate')),
            'signature': hashlib.sha256(json.dumps({**shown, 'photos': photos, 'reader': READER}, sort_keys=True).encode()).hexdigest()}


def day(value: object) -> str | None:
    if value in (None, '', '0', 0):
        return None
    if isinstance(value, (int, float)):
        stamp = int(value)
    elif str(value).isdigit():
        stamp = int(str(value))
    else:
        match = re.match(r'\d{4}-\d{2}-\d{2}', str(value))
        return match.group(0) if match else None
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).date().isoformat()


def best_photo(photo: object) -> str | None:
    item = obj(photo)
    sources = [obj(source) for sources in obj(item.get('mixedSources')).values() for source in arr(sources) if obj(source).get('url')]
    if not sources and item.get('url'):
        return text(item['url'])
    urls = [(text(source['url']) or '', num(source.get('width')) or 0) for source in sources]
    jpeg = [(url, width) for url, width in urls if url.endswith('.jpg')]
    return max(jpeg or urls, key=lambda source: source[1])[0] if sources else None


def photos(items: object) -> list[str]:
    return [url for url in map(best_photo, arr(items)) if url][:MAX_PHOTOS]


def number(value: int | float) -> str:
    return f'{value:,.0f}'


def span(values: Iterable[int | float | None], unit: str, studio: bool = False) -> str | None:
    known = sorted(value for value in values if value is not None)
    if not known:
        return None
    low, high = ('Studio' if studio and value == 0 else number(value) for value in (known[0], known[-1]))
    suffix = '' if high == 'Studio' else f' {unit}'
    return f'{high}{suffix}' if low == high else f'{low} to {high}{suffix}'


def rooms(beds: int | float | None, baths: int | float | None) -> str | None:
    parts = ['Studio' if beds == 0 else f'{number(beds)} bd' if beds is not None else None, f'{baths:g} ba' if baths is not None else None]
    return ', '.join(part for part in parts if part) or None


def unit_fact(price: int | float | None, beds: int | float | None, baths: int | float | None, sqft: int | float | None,
              available_on: str | None, label: str | None, floor: int | None) -> Unit:
    return {'price': price, 'period': 'month', 'bedrooms': beds, 'bathrooms': baths, 'size_sqft': sqft, 'available_on': available_on,
            'label': label, 'floor': floor}


def count(value: object) -> int | None:
    """A positive whole number of floors, as a number or digits, or None."""
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def finite(value: object) -> TypeGuard[int | float]:
    """A number, not a boolean, that is neither infinite nor NaN."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def coordinates(place: dict[str, object]) -> Coordinates:
    latitude, longitude = place.get('latitude'), place.get('longitude')
    if finite(latitude) and finite(longitude) and abs(latitude) <= 90 and abs(longitude) <= 180:
        return {'latitude': latitude, 'longitude': longitude}
    return {'latitude': None, 'longitude': None}


def building_amenities(building: dict[str, object]) -> list[str]:
    amenities: list[object] = []
    structured = obj(building.get('structuredAmenities'))
    for group in map(obj, arr(obj(structured.get('buildingAmenities')).get('amenityGroups'))):
        amenities += arr(group.get('amenities'))
    for section in ('unitFeatures', 'policies', 'specialFeatures'):
        value = structured.get(section)
        if is_array(value):
            amenities += value
            continue
        for group in map(obj, arr(obj(value).get('amenityGroups'))):
            if group.get('amenities'):
                amenities.append(f'{group["title"]}: {", ".join(texts(group["amenities"]))}')
    pets = obj(building.get('amenityDetails')).get('pets')
    if pets:
        amenities.append(f'Pets allowed: {", ".join(texts(pets))}')
    return unique(amenities)


def agent(name: str | None, phone: str | None, email: str | None, contact_form: str | None) -> Agent:
    """The agent fields the site states, in this order."""
    found: Agent = {}
    if name:
        found['name'] = name
    if phone:
        found['phone'] = phone
    if email:
        found['email'] = email
    if contact_form:
        found['contact_form'] = contact_form
    return found


def unique(values: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip()))


def offer(source: dict[str, object], plan: dict[str, object], label: str | None, price: int | float | None) -> Offer:
    """label names the offer in its description line: the unit's number, or the floor plan's name for a plan without units."""
    return {'zpid': str(source.get('zpid')), 'plan': str(plan.get('zpid')), 'label': label, 'unit': text(source.get('unitNumber')), 'price': price,
            'beds': num(source.get('beds')), 'baths': num(source.get('baths')), 'sqft': num(source.get('sqft')), 'floor': count(source.get('floor')),
            'available_on': day(source.get('availableFrom')), 'photos': arr(source.get('photos'))}


def offers(building: dict[str, object]) -> list[Offer]:
    """Every rentable offer of a building: each unit for rent outside floor plans, each unit of a floor plan, and each
    floor plan Zillow lists with a price but no units."""
    found = [offer({**unit, 'photos': [unit['photo']] if unit.get('photo') else []}, unit, text(unit.get('unitNumber')), num(unit.get('price')))
             for unit in map(obj, arr(building.get('ungroupedUnits'))) if unit.get('listingType') == 'FOR_RENT']
    for plan in map(obj, arr(building.get('floorPlans'))):
        units = [obj(unit) for unit in arr(plan.get('units'))]
        found += [offer(unit, plan, text(unit.get('unitNumber')), num(unit.get('price'))) for unit in units]
        if not units and plan.get('minPrice'):
            found.append(offer(plan, plan, text(plan.get('name')), num(plan['minPrice'])))
    return found


def offer_line(offer: Offer) -> str:
    size = f'{number(offer["sqft"])} sqft' if offer['sqft'] else 'size not listed'
    price = f'${number(offer["price"])} a month' if offer['price'] else 'price not listed'
    return f'{rooms(offer["beds"], offer["baths"])}, {size}: {price}, available {offer["available_on"] or "now"} ({offer["label"] or "unit"})'


def read_building(card: ListingPage, building: dict[str, object]) -> tuple[SiteListing, Facts]:
    available = offers(building)
    selected: list[Offer] = available if card['building'] else [offer for offer in available if card['zpid'] in (offer['zpid'], offer['plan'])]
    if not selected:
        raise Gone(card['url'])
    if not card['building']:
        selected = [{**offer, 'available_on': offer['available_on'] or card.get('available_on')} for offer in selected]
    single = len(selected) == 1 and not card['building']
    description = (text(building.get('description')) or '').strip()
    if not single:
        description = '\n\n'.join(part for part in [description, 'Available units:\n' + '\n'.join(map(offer_line, selected[:MAX_UNIT_LINES]))] if part)
    labels = {offer['label'] for offer in selected}
    name = text(building.get('buildingName'))
    title = name if card['building'] or len(labels) != 1 else ', '.join(filter(None, [name, *labels]))
    currency = text(building.get('currency')) or 'USD'
    listing: SiteListing = {
        'id': card['id'], 'title': title or text(building.get('fullAddress')), 'address': text(building.get('fullAddress')),
        'price': min((offer['price'] for offer in selected if offer['price']), default=None), 'currency': currency,
        'rental_period': 'month', 'description': description or None, 'amenities': building_amenities(building),
        'rooms': rooms(selected[0]['beds'], selected[0]['baths']) if single else span([offer['beds'] for offer in selected], 'bd', studio=True),
        'size': (f'{number(selected[0]["sqft"])} sqft' if selected[0]['sqft'] else None) if single else span([offer['sqft'] for offer in selected], 'sqft'),
        'photos': (photos(selected[0]['photos']) if single else []) or photos(building.get('photos')),
        'sources': [{'label': NAME, 'url': card['url']}],
        'agent': agent(text(obj(building.get('contactInfo')).get('agentFullName')) or text(building.get('buildingName')),
                       text(building.get('buildingPhoneNumber')), None, card['url']),
    }
    units = [unit_fact(offer['price'], offer['beds'], offer['baths'], offer['sqft'], offer['available_on'], offer['unit'], offer['floor'])
             for offer in selected]
    return listing, {'currency': listing['currency'], **coordinates(building), 'stories': None, 'units': units}


def home_amenities(facts: dict[str, object]) -> list[str]:
    amenities: list[object] = [f'{fact["factLabel"]}: {fact["factValue"]}' for fact in map(obj, arr(facts.get('atAGlanceFacts'))) if fact.get('factValue')]
    amenities += arr(facts.get('appliances'))
    for label, key in (('Laundry', 'laundryFeatures'), ('Parking', 'parkingFeatures'), ('Interior', 'interiorFeatures'),
                       ('Exterior', 'exteriorFeatures'), ('Community', 'communityFeatures')):
        if facts.get(key):
            amenities.append(f'{label}: {", ".join(texts(facts[key]))}')
    return unique(amenities)


def read_home(card: ListingPage, home: dict[str, object]) -> tuple[SiteListing, Facts]:
    if home.get('homeStatus') != 'FOR_RENT':
        raise Gone(card['url'])
    facts = obj(home.get('resoFacts'))
    attribution = obj(home.get('attributionInfo'))
    sqft = num(home.get('livingArea')) if home.get('livingAreaUnitsShort', 'sqft') == 'sqft' else None
    locality = ' '.join(filter(None, [text(home.get('state')), text(home.get('zipcode'))]))
    address = ', '.join(part for part in [text(home.get('streetAddress')), text(home.get('city')), locality] if part)
    listing: SiteListing = {
        'id': card['id'], 'title': text(home.get('streetAddress')), 'address': address, 'price': num(home.get('price')),
        'currency': text(home.get('currency')) or 'USD', 'rental_period': 'month', 'rooms': rooms(num(home.get('bedrooms')), num(home.get('bathrooms'))),
        'size': f'{number(sqft)} sqft' if sqft else None, 'description': text(home.get('description')), 'amenities': home_amenities(facts),
        'photos': photos(home.get('originalPhotos')), 'sources': [{'label': NAME, 'url': card['url']}],
        'agent': agent(text(attribution.get('agentName')), text(attribution.get('agentPhoneNumber')), text(attribution.get('agentEmail')), card['url']),
    }
    available_on = day(facts.get('availabilityDate')) or card.get('available_on')
    unit = unit_fact(num(home.get('price')), num(home.get('bedrooms')), num(home.get('bathrooms')), sqft, available_on, None, count(facts.get('entryLevel')))
    return listing, {'currency': listing['currency'], **coordinates(home), 'stories': count(facts.get('storiesTotal')), 'units': [unit]}


def read(card: ListingPage, client: Fetcher) -> tuple[SiteListing, Facts]:
    page = fetch(client, card['url'])
    try:
        props = obj(member(page_data(page, card['url']), 'props', 'pageProps', 'componentProps'))
        building = obj(obj(props.get('initialReduxState')).get('gdp')).get('building')
        if building:
            return read_building(card, obj(building))
        cache = props.get('gdpClientCache')
        if cache:
            entries = obj(loads(cache) if isinstance(cache, str) else cache)
            home = next((obj(entry)['property'] for entry in entries.values() if obj(entry).get('property')), None)
            if home:
                return read_home(card, obj(home))
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise Changed(f'Zillow listing page changed shape: {error!r}') from None
    raise Changed(f'{card["url"]} is neither a building nor a home page.')


def reread(listing: SourcedListing, client: Fetcher) -> tuple[SiteListing, Facts]:
    identifier = listing['id'].split(':', 1)[1]
    building = identifier.startswith('building-')
    return read({'id': listing['id'], 'url': listing['sources'][0]['url'], 'building': building,
                 'zpid': None if building else identifier, 'available_on': None}, client)
