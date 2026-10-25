import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/search/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/gallery/scripts'))

from sites import site_for, site_named, zillow  # noqa: E402
from sites.errors import Blocked, Changed, Gone  # noqa: E402
from store import validate_listing  # noqa: E402

CRITERIA = {
    'location': ['Lakeview, Chicago, IL'],
    'price': {'min': None, 'max': 2400, 'currency': 'USD', 'period': 'month'},
    'size': {'min': 65, 'max': None, 'unit': 'm2'},
    'rooms': {'min': 2, 'max': 3},
    'move_in': None,
    'other': [],
}
LAKEVIEW = {'regionId': 269591, 'regionType': 8}


def page(data):
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


def search_page(cards, total, pages=1):
    return page({'props': {'pageProps': {'searchPageState': {'cat1': {
        'searchList': {'totalResultCount': total, 'resultsPerPage': 41, 'totalPages': pages},
        'searchResults': {'listResults': cards, 'relaxedResults': [{'zpid': 'relaxed'}]}}}}}})


def unit_card(zpid, price=2100, **changes):
    return {'zpid': str(zpid), 'id': str(zpid), 'detailUrl': f'https://www.zillow.com/apartments/chicago-il/the-courtyard/{zpid}x/',
            'statusText': 'Apartment for rent', 'price': f'${price:,}/mo', 'unformattedPrice': price,
            'address': '100 W Example St #2, Chicago, IL 60657', 'beds': 2, 'baths': 1, 'area': 850,
            'availabilityDate': '2026-11-15 00:00:00',
            'carouselPhotosComposable': {'photoData': [{'photoKey': 'aaa'}, {'photoKey': 'bbb'}]},
            'hdpData': {'homeInfo': {'zpid': int(zpid), 'price': price, 'bedrooms': 2, 'bathrooms': 1, 'livingArea': 850}},
            **changes}


def building_card(lot, **changes):
    return {'zpid': '41.94--87.65', 'id': '41.94--87.65', 'lotId': lot, 'isBuilding': True,
            'detailUrl': f'/apartments/chicago-il/lake-tower/{lot}b/', 'statusText': 'Lake Tower', 'buildingName': 'Lake Tower',
            'address': '300 W Example Ave, Chicago, IL', 'units': [{'price': '$1,950+', 'beds': '2', 'roomForRent': False}],
            'carouselPhotosComposable': {'photoData': [{'photoKey': 'ccc'}]}, **changes}


def photo(key):
    return {'caption': None, 'photoKey': key, 'mixedSources': {'jpeg': [
        {'url': f'https://photos.zillowstatic.com/fp/{key}-cc_ft_384.jpg', 'width': 384},
        {'url': f'https://photos.zillowstatic.com/fp/{key}-cc_ft_1536.jpg', 'width': 1536}]}}


def floor_unit(zpid, price, beds, sqft, available='0', number='Unit 2A'):
    return {'unitNumber': number, 'zpid': str(zpid), 'beds': beds, 'baths': 1, 'price': price, 'sqft': sqft,
            'availableFrom': available, 'photos': [photo(f'unit{zpid}')]}


def floor_plan(zpid, price, beds, sqft, name='2 Bedroom'):
    return {'zpid': str(zpid), 'name': name, 'beds': beds, 'baths': 1, 'minPrice': price, 'maxPrice': price, 'sqft': sqft,
            'availableFrom': '0', 'units': [], 'photos': [photo(f'plan{zpid}')]}


def building_page(units, plans=()):
    building = {
        'buildingName': 'Lake Tower', 'fullAddress': '300 W Example Ave, Chicago, IL 60657', 'currency': 'USD', 'latitude': 41.94, 'longitude': -87.65,
        'description': 'A tower by the lake.', 'buildingPhoneNumber': '(312) 555-0100',
        'contactInfo': {'agentFullName': None},
        'photos': [photo('b1'), photo('b2')],
        'floorPlans': ([{'zpid': '999', 'units': units}] if units else []) + list(plans),
        'structuredAmenities': {
            'buildingAmenities': {'amenityGroups': [{'title': 'Services & facilities', 'amenities': ['Elevator', 'Bicycle storage']}]},
            'unitFeatures': {'amenityGroups': [{'title': 'Laundry', 'amenities': ['In unit']}]},
            'policies': None, 'specialFeatures': None},
        'amenityDetails': {'pets': ['Cats', 'Large dogs']},
        'rentalUnitsSummary': {'availableUnitCount': len(units)},
    }
    return page({'props': {'pageProps': {'componentProps': {'initialReduxState': {'gdp': {'building': building}}}}}})


def home_page(zpid, status='FOR_RENT', price=2385, latitude=41.927895, longitude=-87.723366, **facts):
    key = f'ForRentShopperPlatformFullRenderQuery{{"zpid":{zpid}}}'
    home = {'zpid': zpid, 'homeStatus': status, 'price': price, 'currency': 'USD', 'bedrooms': 2, 'bathrooms': 1, 'livingArea': 700,
            'livingAreaUnitsShort': 'sqft', 'streetAddress': '2476 N Example Ave APT 1F', 'latitude': latitude, 'longitude': longitude, 'city': 'Chicago', 'state': 'IL', 'zipcode': '60614',
            'description': 'New kitchen.\nCourtyard with grill.', 'hdpUrl': f'/homedetails/2476-N-Example-Ave/{zpid}_zpid/',
            'attributionInfo': {'agentName': 'Pat Manager', 'agentPhoneNumber': '312-555-0199', 'agentEmail': None},
            'originalPhotos': [photo('h1')],
            'resoFacts': {'availabilityDate': 1793318400000, 'atAGlanceFacts': [{'factLabel': 'Pets', 'factValue': 'Cats, Small Dogs'},
                                                                              {'factLabel': 'Heating', 'factValue': None}],
                          'appliances': ['Dishwasher'], 'laundryFeatures': ['Shared'], 'parkingFeatures': [], 'interiorFeatures': None, **facts}}
    return page({'props': {'pageProps': {'componentProps': {'gdpClientCache': json.dumps({key: {'property': home}})}}}})


def unit_fact(price, bedrooms, size, available_on, label, bathrooms=1):
    return {'price': price, 'period': 'month', 'bedrooms': bedrooms, 'bathrooms': bathrooms, 'size_sqft': size, 'available_on': available_on,
            'label': label, 'floor': None}


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return self.responses.pop(0)


def query_state(url):
    return json.loads(unquote(parse_qs(urlsplit(url).query)['searchQueryState'][0]))


class SiteRegistryTests(unittest.TestCase):
    def test_sites_are_found_by_the_host_the_user_named_and_by_listing_id(self):
        self.assertIs(site_for('https://www.zillow.com'), zillow)
        self.assertIs(site_for('https://zillow.com/'), zillow)
        self.assertIsNone(site_for('https://www.apartments.com'))
        self.assertIs(site_named('zillow'), zillow)
        self.assertIsNone(site_named('apartments'))


class ZillowSearchTests(unittest.TestCase):
    def test_each_area_is_resolved_once_and_the_criteria_become_site_filters(self):
        client = FakeClient((200, json.dumps({'results': [
            {'display': '1 Lakeview Ave, Chicago', 'resultType': 'Address', 'metaData': {}},
            {'display': 'Lakeview, Chicago, IL', 'resultType': 'Region', 'metaData': {'regionId': 269591, 'regionType': 'neighborhood'}}]})))
        regions = {}
        searches = zillow.searches(CRITERIA, client, regions)
        self.assertEqual(regions, {'Lakeview, Chicago, IL': LAKEVIEW})
        self.assertIn(quote('Lakeview, Chicago, IL'), client.urls[0])
        self.assertEqual(zillow.searches(CRITERIA, client, regions), searches)
        self.assertEqual(len(client.urls), 1)
        [search] = searches
        self.assertEqual(search['area'], 'Lakeview, Chicago, IL')
        filters = search['filters']
        self.assertEqual(filters['mp'], {'min': None, 'max': 2400})
        self.assertEqual(filters['beds'], {'min': 2, 'max': 3})
        self.assertEqual(filters['sqft'], {'min': 699, 'max': None})
        self.assertEqual(filters['fr'], {'value': True})
        self.assertEqual(filters['sort'], {'value': 'priorityscore'})

    def test_weekly_prices_become_monthly_and_other_currencies_are_not_filtered(self):
        weekly = {**CRITERIA, 'price': {'min': 300, 'max': 600, 'currency': 'USD', 'period': 'week'}}
        self.assertEqual(zillow.filters(weekly)['mp'], {'min': 1300, 'max': 2600})
        euros = {**CRITERIA, 'price': {'min': None, 'max': 2000, 'currency': 'EUR', 'period': 'month'}}
        self.assertNotIn('mp', zillow.filters(euros))
        self.assertNotIn('mp', zillow.filters({**CRITERIA, 'price': {'min': None, 'max': None}}))

    def test_an_area_the_site_does_not_know_is_reported(self):
        client = FakeClient((200, json.dumps({'results': [{'display': 'x', 'resultType': 'Address', 'metaData': {}}]})))
        with self.assertRaisesRegex(LookupError, 'Atlantis'):
            zillow.searches({**CRITERIA, 'location': ['Atlantis']}, client, {})

    def test_pages_follow_the_site_order_and_skip_relaxed_results(self):
        search = {'area': 'Lakeview, Chicago, IL', 'region': LAKEVIEW, 'filters': zillow.filters(CRITERIA)}
        client = FakeClient((200, search_page([unit_card(1), building_card(77)], 43, pages=2)),
                            (200, search_page([unit_card(2)], 43, pages=2)))
        pages = list(zillow.pages(search, client))
        self.assertEqual([[card['id'] for card in cards] for cards in pages], [['zillow:1', 'zillow:building-77'], ['zillow:2']])
        self.assertEqual(urlsplit(client.urls[0]).path, '/homes/for_rent/')
        self.assertEqual(urlsplit(client.urls[1]).path, '/homes/for_rent/2_p/')
        self.assertEqual(query_state(client.urls[1])['pagination'], {'currentPage': 2})
        self.assertEqual(query_state(client.urls[0])['regionSelection'], [LAKEVIEW])
        self.assertEqual(pages[0][1]['url'], 'https://www.zillow.com/apartments/chicago-il/lake-tower/77b/')

    def test_more_results_than_the_site_pages_through_are_split_into_price_bands(self):
        search = {'area': 'Lakeview, Chicago, IL', 'region': LAKEVIEW, 'filters': zillow.filters(CRITERIA)}
        client = FakeClient((200, search_page([unit_card(1)], 900, pages=20)),
                            (200, search_page([unit_card(2)], 60, pages=2)),
                            (200, search_page([unit_card(3)], 600, pages=15)),
                            (200, search_page([unit_card(4)], 60, pages=2)),
                            (200, search_page([unit_card(5)], 600, pages=15)))
        pages = zillow.pages(search, client)
        first = [[card['id'] for card in next(pages)] for _ in range(5)]
        self.assertEqual(first, [['zillow:1'], ['zillow:2'], ['zillow:3'], ['zillow:4'], ['zillow:5']])
        self.assertEqual(query_state(client.urls[0])['filterState']['mp'], {'min': None, 'max': 2400})
        self.assertEqual(query_state(client.urls[1])['filterState']['mp'], {'min': None, 'max': 1200})
        self.assertEqual(query_state(client.urls[2])['filterState']['mp'], {'min': 1201, 'max': 2400})
        self.assertEqual(urlsplit(client.urls[3]).path, '/homes/for_rent/2_p/')
        self.assertEqual(query_state(client.urls[3])['filterState']['mp'], {'min': None, 'max': 1200})
        self.assertEqual(urlsplit(client.urls[4]).path, '/homes/for_rent/2_p/')
        self.assertEqual(query_state(client.urls[4])['filterState']['mp'], {'min': 1201, 'max': 2400})

    def test_card_signature_follows_what_the_card_shows(self):
        first = zillow.card(unit_card(1))
        self.assertEqual(zillow.card(unit_card(1))['signature'], first['signature'])
        self.assertNotEqual(zillow.card(unit_card(1, price=2000))['signature'], first['signature'])
        photos = {'photoData': [{'photoKey': 'zzz'}]}
        self.assertNotEqual(zillow.card(unit_card(1, carouselPhotosComposable=photos))['signature'], first['signature'])

    def test_a_challenge_page_or_refusal_is_a_block(self):
        search = {'area': 'Lakeview, Chicago, IL', 'region': LAKEVIEW, 'filters': zillow.filters(CRITERIA)}
        challenge = '<html><head><meta name="description" content="px-captcha"></head></html>'
        for response in [(403, challenge), (200, challenge), (429, '')]:
            with self.subTest(response=response[0]), self.assertRaises(Blocked):
                list(zillow.pages(search, FakeClient(response)))

    def test_a_page_without_its_data_is_a_changed_site(self):
        search = {'area': 'Lakeview, Chicago, IL', 'region': LAKEVIEW, 'filters': zillow.filters(CRITERIA)}
        broken = '<script id="__NEXT_DATA__" type="application/json">{"props": </script>'
        no_summary = search_page([unit_card(1)], 1).replace('"resultsPerPage": 41, ', '')
        for response in [(200, '<html>new design</html>'), (200, broken), (200, no_summary), (404, 'Not found')]:
            with self.subTest(response=response[1][:30]), self.assertRaises(Changed):
                list(zillow.pages(search, FakeClient(response)))
        with self.assertRaises(Changed):
            zillow.read(zillow.card(unit_card(12)), FakeClient((200, broken)))


class ZillowReadTests(unittest.TestCase):
    def test_a_building_becomes_one_listing_with_every_unit_as_facts(self):
        units = [floor_unit(11, 1950, 2, 900, number='Unit 4B'), floor_unit(12, 1500, 0, 450, available='2026-12-15'),
                 floor_unit(13, 3100, 3, 1300, available=str(1795000000000))]
        card = zillow.card(building_card(77))
        listing, facts = zillow.read(card, FakeClient((200, building_page(units))))
        validate_listing(listing)
        self.assertEqual(listing['id'], 'zillow:building-77')
        self.assertEqual(listing['title'], 'Lake Tower')
        self.assertEqual(listing['address'], '300 W Example Ave, Chicago, IL 60657')
        self.assertEqual((listing['price'], listing['currency'], listing['rental_period']), (1500, 'USD', 'month'))
        self.assertEqual(listing['rooms'], 'Studio to 3 bd')
        self.assertEqual(listing['size'], '450 to 1,300 sqft')
        self.assertIn('A tower by the lake.', listing['description'])
        self.assertIn('2 bd, 1 ba, 900 sqft: $1,950 a month, available now (Unit 4B)', listing['description'])
        self.assertEqual(listing['amenities'], ['Elevator', 'Bicycle storage', 'Laundry: In unit', 'Pets allowed: Cats, Large dogs'])
        self.assertEqual(listing['photos'], ['https://photos.zillowstatic.com/fp/b1-cc_ft_1536.jpg', 'https://photos.zillowstatic.com/fp/b2-cc_ft_1536.jpg'])
        self.assertEqual(listing['sources'], [{'label': 'Zillow', 'url': card['url']}])
        self.assertEqual(listing['agent'], {'name': 'Lake Tower', 'phone': '(312) 555-0100', 'contact_form': card['url']})
        self.assertEqual(facts['currency'], 'USD')
        self.assertEqual(facts['units'], [
            unit_fact(1950, 2, 900, None, 'Unit 4B'), unit_fact(1500, 0, 450, '2026-12-15', 'Unit 2A'),
            unit_fact(3100, 3, 1300, '2026-11-18', 'Unit 2A')])
        self.assertEqual((facts['latitude'], facts['longitude'], facts['stories']), (41.94, -87.65, None))

    def test_a_unit_card_is_read_from_its_building_page(self):
        card = zillow.card(unit_card(12))
        listing, facts = zillow.read(card, FakeClient((200, building_page([floor_unit(11, 1950, 2, 900), floor_unit(12, 2100, 2, 850, number='Unit 9C')]))))
        validate_listing(listing)
        self.assertEqual(listing['id'], 'zillow:12')
        self.assertEqual(listing['title'], 'Lake Tower, Unit 9C')
        self.assertEqual((listing['price'], listing['rooms'], listing['size']), (2100, '2 bd, 1 ba', '850 sqft'))
        self.assertEqual(listing['photos'], ['https://photos.zillowstatic.com/fp/unit12-cc_ft_1536.jpg'])
        self.assertEqual(facts['units'], [unit_fact(2100, 2, 850, '2026-11-15', 'Unit 9C')])

    def test_floor_plans_listed_without_units_are_offers_and_a_card_can_be_one_plan(self):
        plans = [floor_plan(30, 1350, 0, 265, name='Studio'), floor_plan(31, 2350, 2, 730, name='2 Bedroom')]
        building, facts = zillow.read(zillow.card(building_card(77)), FakeClient((200, building_page([], plans=plans))))
        validate_listing(building)
        self.assertEqual((building['price'], building['rooms']), (1350, 'Studio to 2 bd'))
        self.assertEqual([unit['price'] for unit in facts['units']], [1350, 2350])
        self.assertIn('2 bd, 1 ba, 730 sqft: $2,350 a month, available now (2 Bedroom)', building['description'])
        listing, facts = zillow.read(zillow.card(unit_card(31)), FakeClient((200, building_page([], plans=plans))))
        validate_listing(listing)
        self.assertEqual((listing['title'], listing['price'], listing['rooms'], listing['size']), ('Lake Tower, 2 Bedroom', 2350, '2 bd, 1 ba', '730 sqft'))
        self.assertEqual(listing['photos'], ['https://photos.zillowstatic.com/fp/plan31-cc_ft_1536.jpg'])
        self.assertEqual(facts['units'], [unit_fact(2350, 2, 730, '2026-11-15', None)])

    def test_units_listed_outside_floor_plans_are_offers(self):
        ungrouped = [{'listingType': 'FOR_RENT', 'unitNumber': 'Unit 1F', 'zpid': '41', 'price': 2100, 'beds': 3, 'baths': 2, 'sqft': None,
                      'photo': {'url': 'https://photos.zillowstatic.com/fp/u41-p_i.jpg'}},
                     {'listingType': 'OFF_MARKET', 'unitNumber': 'Unit 2F', 'zpid': '42', 'price': 1900, 'beds': 2, 'baths': 1, 'sqft': 800}]
        page = building_page([]).replace('"floorPlans": []', '"floorPlans": [], "ungroupedUnits": ' + json.dumps(ungrouped))
        building, facts = zillow.read(zillow.card(building_card(77)), FakeClient((200, page)))
        self.assertEqual((building['price'], building['rooms']), (2100, '3 bd'))
        self.assertEqual(facts['units'], [unit_fact(2100, 3, None, None, 'Unit 1F', bathrooms=2)])
        unit, _ = zillow.read(zillow.card(unit_card(41)), FakeClient((200, page)))
        self.assertEqual((unit['title'], unit['photos']), ('Lake Tower, Unit 1F', ['https://photos.zillowstatic.com/fp/u41-p_i.jpg']))

    def test_special_features_listed_as_text_and_photos_given_as_plain_links_are_read(self):
        plan = {**floor_plan(31, 2350, 2, 730), 'photos': [{'url': 'https://photos.zillowstatic.com/fp/p31-o_a.jpg'}]}
        page = building_page([], plans=[plan]).replace('"specialFeatures": null', '"specialFeatures": ["Closet: Walk In Closet"]')
        listing, _ = zillow.read(zillow.card(unit_card(31)), FakeClient((200, page)))
        self.assertIn('Closet: Walk In Closet', listing['amenities'])
        self.assertEqual(listing['photos'], ['https://photos.zillowstatic.com/fp/p31-o_a.jpg'])

    def test_blank_amenities_are_left_out(self):
        page = home_page(5, appliances=['Dishwasher', '', '  '])
        listing, _ = zillow.read(zillow.card(unit_card(5, detailUrl='/homedetails/x/5_zpid/')), FakeClient((200, page)))
        validate_listing(listing)
        self.assertIn('Dishwasher', listing['amenities'])
        self.assertNotIn('', listing['amenities'])

    def test_amenities_are_listed_once(self):
        page = home_page(5, parkingFeatures=['Garage'], atAGlanceFacts=[{'factLabel': 'Pets', 'factValue': 'Cats, Small Dogs'},
                                                                         {'factLabel': 'Laundry', 'factValue': 'Shared'}])
        listing, _ = zillow.read(zillow.card(unit_card(5, detailUrl='/homedetails/x/5_zpid/')), FakeClient((200, page)))
        self.assertEqual(listing['amenities'], ['Pets: Cats, Small Dogs', 'Laundry: Shared', 'Dishwasher', 'Parking: Garage'])

    def test_a_home_page_is_read_with_its_agent_and_facts(self):
        card = zillow.card(unit_card(2106155678, detailUrl='https://www.zillow.com/homedetails/2476-N-Example-Ave/2106155678_zpid/'))
        listing, facts = zillow.read(card, FakeClient((200, home_page(2106155678))))
        validate_listing(listing)
        self.assertEqual(listing['address'], '2476 N Example Ave APT 1F, Chicago, IL 60614')
        self.assertEqual(listing['agent'], {'name': 'Pat Manager', 'phone': '312-555-0199', 'contact_form': card['url']})
        self.assertEqual(listing['amenities'], ['Pets: Cats, Small Dogs', 'Dishwasher', 'Laundry: Shared'])
        self.assertEqual(listing['description'], 'New kitchen.\nCourtyard with grill.')
        self.assertEqual(facts['units'], [unit_fact(2385, 2, 700, '2026-10-30', None)])
        self.assertEqual((facts['latitude'], facts['longitude'], facts['stories']), (41.927895, -87.723366, None))

    def test_a_home_page_gives_the_floor_and_building_height_its_facts_state(self):
        card = zillow.card(unit_card(5, detailUrl='/homedetails/x/5_zpid/'))
        _, facts = zillow.read(card, FakeClient((200, home_page(5, entryLevel='3', storiesTotal=4))))
        self.assertEqual((facts['units'][0]['floor'], facts['stories']), (3, 4))
        for level, stories in [('Ground', 'four'), (0, None), (2.5, 0)]:
            with self.subTest(level=level):
                _, facts = zillow.read(card, FakeClient((200, home_page(5, entryLevel=level, storiesTotal=stories))))
                self.assertEqual((facts['units'][0]['floor'], facts['stories']), (None, None))

    def test_a_building_unit_gives_the_floor_its_page_states(self):
        units = [{**floor_unit(11, 1950, 2, 900), 'floor': 7}, {**floor_unit(12, 2100, 2, 850), 'floor': None}]
        _, facts = zillow.read(zillow.card(building_card(77)), FakeClient((200, building_page(units))))
        self.assertEqual([unit['floor'] for unit in facts['units']], [7, None])

    def test_coordinates_outside_the_globe_or_missing_are_unknown(self):
        card = zillow.card(unit_card(5, detailUrl='/homedetails/x/5_zpid/'))
        for latitude, longitude in [(None, -87.7), (95.0, -87.7), (41.9, 'west'), (41.9, float('nan')), (True, -87.7)]:
            with self.subTest(latitude=latitude, longitude=longitude):
                _, facts = zillow.read(card, FakeClient((200, home_page(5, latitude=latitude, longitude=longitude))))
                self.assertEqual((facts['latitude'], facts['longitude']), (None, None))
        page = building_page([floor_unit(11, 1950, 2, 900)]).replace('"latitude": 41.94, "longitude": -87.65, ', '')
        _, facts = zillow.read(zillow.card(building_card(77)), FakeClient((200, page)))
        self.assertEqual((facts['latitude'], facts['longitude']), (None, None))

    def test_a_new_reader_version_reads_every_card_again(self):
        before = zillow.card(unit_card(1))['signature']
        original = zillow.READER
        try:
            zillow.READER = original + 1
            self.assertNotEqual(zillow.card(unit_card(1))['signature'], before)
        finally:
            zillow.READER = original

    def test_listings_no_longer_for_rent_are_gone(self):
        unit = zillow.card(unit_card(12))
        building = zillow.card(building_card(77))
        cases = [(unit, (200, building_page([floor_unit(11, 1950, 2, 900)]))),
                 (building, (200, building_page([]))),
                 (building, (200, building_page([], plans=[floor_plan(30, None, 2, 700)]))),
                 (zillow.card(unit_card(5, detailUrl='/homedetails/x/5_zpid/')), (200, home_page(5, status='OFF_MARKET'))),
                 (unit, (404, 'Not found')), (unit, (410, ''))]
        for card, response in cases:
            with self.subTest(card=card['id'], status=response[0]), self.assertRaises(Gone):
                zillow.read(card, FakeClient(response))

    def test_a_stored_listing_is_reread_from_its_source_link(self):
        listing, _ = zillow.read(zillow.card(unit_card(12)), FakeClient((200, building_page([floor_unit(12, 2100, 2, 850)]))))
        again, facts = zillow.reread(listing, FakeClient((200, building_page([floor_unit(12, 2200, 2, 850)]))))
        self.assertEqual((again['id'], again['price']), ('zillow:12', 2200))
        with self.assertRaises(Gone):
            zillow.reread(listing, FakeClient((200, building_page([floor_unit(11, 1950, 2, 900)]))))


if __name__ == '__main__':
    unittest.main()
