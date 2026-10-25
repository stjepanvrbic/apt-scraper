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
from datetime import datetime, timezone
from urllib.parse import quote, urljoin

from sites.errors import Blocked, Changed, Gone

NAME = 'Zillow'
HOSTS = {'zillow.com', 'www.zillow.com'}
ORIGIN = 'https://www.zillow.com'
AUTOCOMPLETE = 'https://www.zillowstatic.com/autocomplete/v3/suggestions?q='
REGION_TYPES = {'state': 2, 'county': 4, 'city': 6, 'zipcode': 7, 'neighborhood': 8}
PERIODS_PER_YEAR = {'day': 365, 'week': 52, 'month': 12, 'year': 1}
SQUARE_FEET_PER_SQUARE_METER = 10.7639
RENTALS_ONLY = {'fr': {'value': True}, 'fsba': {'value': False}, 'fsbo': {'value': False}, 'nc': {'value': False},
                'cmsn': {'value': False}, 'auc': {'value': False}, 'fore': {'value': False}}
DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
MAX_PHOTOS = 40
MAX_UNIT_LINES = 40


def fetch(client, url):
    status, text = client.get(url)
    if status in (403, 429) or 'px-captcha' in text:
        raise Blocked(f'Zillow refused {url} with a bot check (HTTP {status}).')
    if status in (404, 410):
        raise Gone(url)
    if status != 200:
        raise ConnectionError(f'Zillow answered HTTP {status} for {url}.')
    return text


def page_data(text, url):
    match = DATA.search(text)
    if not match:
        raise Changed(f'{url} has no page data.')
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise Changed(f'{url} has page data that is not JSON: {error}') from None


def resolve(area, client):
    results = json.loads(fetch(client, AUTOCOMPLETE + quote(area))).get('results', [])
    for result in results:
        meta = result.get('metaData', {})
        if result.get('resultType') == 'Region' and meta.get('regionType') in REGION_TYPES:
            return {'regionId': meta['regionId'], 'regionType': REGION_TYPES[meta['regionType']]}
    raise LookupError(f'Zillow knows no area called "{area}".')


def limits(low, high, scale=1):
    return {'min': None if low is None else math.floor(low * scale), 'max': None if high is None else math.ceil(high * scale)}


def filters(criteria):
    state = {**RENTALS_ONLY, 'sort': {'value': 'priorityscore'}}
    price = criteria.get('price', {})
    if price.get('currency') == 'USD' and price.get('period') in PERIODS_PER_YEAR and (price.get('min'), price.get('max')) != (None, None):
        state['mp'] = limits(price.get('min'), price.get('max'), PERIODS_PER_YEAR[price['period']] / 12)
    rooms = criteria.get('rooms', {})
    if (rooms.get('min'), rooms.get('max')) != (None, None):
        state['beds'] = limits(rooms.get('min'), rooms.get('max'))
    size = criteria.get('size', {})
    if (size.get('min'), size.get('max')) != (None, None):
        state['sqft'] = limits(size.get('min'), size.get('max'), SQUARE_FEET_PER_SQUARE_METER if size.get('unit') == 'm2' else 1)
    return state


def searches(criteria, client, regions):
    found = []
    for area in criteria['location']:
        if area not in regions:
            regions[area] = resolve(area, client)
        found.append({'area': area, 'region': regions[area], 'filters': filters(criteria)})
    return found


def search_url(search, band, number):
    state = {'pagination': {'currentPage': number} if number > 1 else {}, 'isMapVisible': False, 'isListVisible': True,
             'regionSelection': [search['region']], 'filterState': {**search['filters'], 'mp': {'min': band[0], 'max': band[1]}}}
    if band == (None, None):
        del state['filterState']['mp']
    path = '/homes/for_rent/' + (f'{number}_p/' if number > 1 else '')
    return ORIGIN + path + '?searchQueryState=' + quote(json.dumps(state, separators=(',', ':')))


def results_page(client, search, band, number):
    """((total results, results per page, pages), cards) of one result page."""
    url = search_url(search, band, number)
    try:
        listing = page_data(fetch(client, url), url)['props']['pageProps']['searchPageState']['cat1']
        summary = listing['searchList']
        counts = (int(summary['totalResultCount']), int(summary['resultsPerPage']), int(summary['totalPages']))
        return counts, [card(raw) for raw in listing['searchResults']['listResults']]
    except Gone:
        raise Changed(f'Zillow has no search results page at {url}.') from None
    except (KeyError, TypeError, ValueError) as error:
        raise Changed(f'Zillow search results changed shape: {error!r}') from None


def split(band):
    low, high = band
    start = low or 0
    middle = (start + high) // 2 if high is not None else max(start * 2, start + 1000)
    return [(low, middle), (middle + 1, high)]


def pages(search, client):
    """Zillow places the query's first page highest, then the first page of each price band the query is split
    into, then the later pages of every band in turn."""
    mp = search['filters'].get('mp', {})
    opening, later = [(mp.get('min'), mp.get('max'))], []
    while opening or later:
        while opening:
            band = opening.pop(0)
            (total, per_page, last), cards = results_page(client, search, band, 1)
            yield cards
            if total > per_page * last and (band[1] is None or band[1] - (band[0] or 0) > 1):
                opening[0:0] = split(band)
            elif last > 1:
                later.append([band, 2, last])
        for entry in list(later):
            band, number, last = entry
            yield results_page(client, search, band, number)[1]
            entry[1] += 1
            if entry[1] > last:
                later.remove(entry)


def card(raw):
    building = bool(raw.get('isBuilding'))
    photos = [photo.get('photoKey') for photo in (raw.get('carouselPhotosComposable') or {}).get('photoData', [])]
    shown = {key: raw.get(key) for key in ('statusText', 'address', 'price', 'unformattedPrice', 'units', 'beds', 'baths', 'area', 'availabilityDate')}
    return {'id': f'zillow:building-{raw["lotId"]}' if building else f'zillow:{raw["zpid"]}',
            'url': urljoin(ORIGIN, raw['detailUrl']), 'building': building, 'zpid': None if building else str(raw['zpid']),
            'available_on': day(raw.get('availabilityDate')),
            'signature': hashlib.sha256(json.dumps({**shown, 'photos': photos}, sort_keys=True).encode()).hexdigest()}


def day(value):
    if value in (None, '', '0', 0):
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc).date().isoformat()
    match = re.match(r'\d{4}-\d{2}-\d{2}', str(value))
    return match.group(0) if match else None


def best_photo(photo):
    sources = [source for sources in (photo.get('mixedSources') or {}).values() for source in sources if source.get('url')]
    if not sources and photo.get('url'):
        return photo['url']
    jpeg = [source for source in sources if source['url'].endswith('.jpg')]
    return max(jpeg or sources, key=lambda source: source.get('width') or 0)['url'] if sources else None


def photos(items):
    return [url for url in map(best_photo, items or []) if url][:MAX_PHOTOS]


def number(value):
    return f'{value:,.0f}'


def span(values, unit, studio=False):
    values = sorted(value for value in values if value is not None)
    if not values:
        return None
    low, high = ('Studio' if studio and value == 0 else number(value) for value in (values[0], values[-1]))
    suffix = '' if high == 'Studio' else f' {unit}'
    return f'{high}{suffix}' if low == high else f'{low} to {high}{suffix}'


def rooms(beds, baths):
    parts = ['Studio' if beds == 0 else f'{number(beds)} bd' if beds is not None else None, f'{baths:g} ba' if baths is not None else None]
    return ', '.join(part for part in parts if part) or None


def unit_fact(price, beds, sqft, available_on):
    return {'price': price, 'period': 'month', 'bedrooms': beds, 'size_sqft': sqft, 'available_on': available_on}


def building_amenities(building):
    amenities = []
    structured = building.get('structuredAmenities') or {}
    for group in (structured.get('buildingAmenities') or {}).get('amenityGroups', []):
        amenities += group.get('amenities', [])
    for section in ('unitFeatures', 'policies', 'specialFeatures'):
        value = structured.get(section) or {}
        if isinstance(value, list):
            amenities += value
            continue
        for group in value.get('amenityGroups', []):
            if group.get('amenities'):
                amenities.append(f'{group["title"]}: {", ".join(group["amenities"])}')
    pets = (building.get('amenityDetails') or {}).get('pets')
    if pets:
        amenities.append(f'Pets allowed: {", ".join(pets)}')
    return unique(amenities)


def compact(values):
    return {key: value for key, value in values.items() if value}


def unique(values):
    return list(dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip()))


def offer(source, plan, label, price):
    return {'zpid': str(source.get('zpid')), 'plan': str(plan.get('zpid')), 'label': label, 'price': price, 'beds': source.get('beds'),
            'baths': source.get('baths'), 'sqft': source.get('sqft'), 'available_on': day(source.get('availableFrom')), 'photos': source.get('photos')}


def offers(building):
    """Every rentable offer of a building: each unit for rent outside floor plans, each unit of a floor plan, and each
    floor plan Zillow lists with a price but no units."""
    found = [offer({**unit, 'photos': [unit['photo']] if unit.get('photo') else []}, unit, unit.get('unitNumber'), unit.get('price'))
             for unit in building.get('ungroupedUnits') or [] if unit.get('listingType') == 'FOR_RENT']
    for plan in building.get('floorPlans') or []:
        units = plan.get('units') or []
        found += [offer(unit, plan, unit.get('unitNumber'), unit.get('price')) for unit in units]
        if not units and plan.get('minPrice'):
            found.append(offer(plan, plan, plan.get('name'), plan['minPrice']))
    return found


def offer_line(offer):
    size = f'{number(offer["sqft"])} sqft' if offer['sqft'] else 'size not listed'
    price = f'${number(offer["price"])} a month' if offer['price'] else 'price not listed'
    return f'{rooms(offer["beds"], offer["baths"])}, {size}: {price}, available {offer["available_on"] or "now"} ({offer["label"] or "unit"})'


def read_building(card, building):
    available = offers(building)
    selected = available if card['building'] else [offer for offer in available if card['zpid'] in (offer['zpid'], offer['plan'])]
    if not selected:
        raise Gone(card['url'])
    if not card['building']:
        selected = [{**offer, 'available_on': offer['available_on'] or card.get('available_on')} for offer in selected]
    single = len(selected) == 1 and not card['building']
    description = (building.get('description') or '').strip()
    if not single:
        description = '\n\n'.join(part for part in [description, 'Available units:\n' + '\n'.join(map(offer_line, selected[:MAX_UNIT_LINES]))] if part)
    labels = {offer['label'] for offer in selected}
    name = building.get('buildingName')
    title = name if card['building'] or len(labels) != 1 else ', '.join(filter(None, [name, *labels]))
    listing = {
        'id': card['id'], 'title': title or building.get('fullAddress'), 'address': building.get('fullAddress'),
        'price': min((offer['price'] for offer in selected if offer['price']), default=None), 'currency': building.get('currency') or 'USD',
        'rental_period': 'month', 'description': description or None, 'amenities': building_amenities(building),
        'rooms': rooms(selected[0]['beds'], selected[0]['baths']) if single else span([offer['beds'] for offer in selected], 'bd', studio=True),
        'size': (f'{number(selected[0]["sqft"])} sqft' if selected[0]['sqft'] else None) if single else span([offer['sqft'] for offer in selected], 'sqft'),
        'photos': (photos(selected[0]['photos']) if single else []) or photos(building.get('photos')),
        'sources': [{'label': NAME, 'url': card['url']}],
        'agent': compact({'name': (building.get('contactInfo') or {}).get('agentFullName') or building.get('buildingName'),
                          'phone': building.get('buildingPhoneNumber'), 'contact_form': card['url']}),
    }
    units = [unit_fact(offer['price'], offer['beds'], offer['sqft'], offer['available_on']) for offer in selected]
    return listing, {'currency': listing['currency'], 'units': units}


def home_amenities(facts):
    amenities = [f'{fact["factLabel"]}: {fact["factValue"]}' for fact in facts.get('atAGlanceFacts') or [] if fact.get('factValue')]
    amenities += facts.get('appliances') or []
    for label, key in (('Laundry', 'laundryFeatures'), ('Parking', 'parkingFeatures'), ('Interior', 'interiorFeatures'),
                       ('Exterior', 'exteriorFeatures'), ('Community', 'communityFeatures')):
        if facts.get(key):
            amenities.append(f'{label}: {", ".join(facts[key])}')
    return unique(amenities)


def read_home(card, home):
    if home.get('homeStatus') != 'FOR_RENT':
        raise Gone(card['url'])
    facts = home.get('resoFacts') or {}
    attribution = home.get('attributionInfo') or {}
    sqft = home.get('livingArea') if home.get('livingAreaUnitsShort', 'sqft') == 'sqft' else None
    address = ', '.join(part for part in [home.get('streetAddress'), home.get('city'), ' '.join(filter(None, [home.get('state'), home.get('zipcode')]))] if part)
    listing = {
        'id': card['id'], 'title': home.get('streetAddress'), 'address': address, 'price': home.get('price'),
        'currency': home.get('currency') or 'USD', 'rental_period': 'month', 'rooms': rooms(home.get('bedrooms'), home.get('bathrooms')),
        'size': f'{number(sqft)} sqft' if sqft else None, 'description': home.get('description'), 'amenities': home_amenities(facts),
        'photos': photos(home.get('originalPhotos')), 'sources': [{'label': NAME, 'url': card['url']}],
        'agent': compact({'name': attribution.get('agentName'), 'phone': attribution.get('agentPhoneNumber'),
                          'email': attribution.get('agentEmail'), 'contact_form': card['url']}),
    }
    available_on = day(facts.get('availabilityDate')) or card.get('available_on')
    return listing, {'currency': listing['currency'], 'units': [unit_fact(home.get('price'), home.get('bedrooms'), sqft, available_on)]}


def read(card, client):
    text = fetch(client, card['url'])
    try:
        props = page_data(text, card['url'])['props']['pageProps']['componentProps']
        building = ((props.get('initialReduxState') or {}).get('gdp') or {}).get('building')
        if building:
            return read_building(card, building)
        cache = props.get('gdpClientCache')
        if cache:
            cache = json.loads(cache) if isinstance(cache, str) else cache
            home = next((entry['property'] for entry in cache.values() if (entry or {}).get('property')), None)
            if home:
                return read_home(card, home)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise Changed(f'Zillow listing page changed shape: {error!r}') from None
    raise Changed(f'{card["url"]} is neither a building nor a home page.')


def reread(listing, client):
    identifier = listing['id'].split(':', 1)[1]
    building = identifier.startswith('building-')
    return read({'id': listing['id'], 'url': listing['sources'][0]['url'], 'building': building,
                 'zpid': None if building else identifier, 'available_on': None}, client)
