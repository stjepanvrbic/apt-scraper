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

When no unit has a price, the features describe the only unit, or none when there are several. Any value
the listing does not state is null. Days since first seen depends on the day it is counted on, so
ListingStore.code_features counts it when read.
"""

import json
import re

PERIODS_PER_YEAR = {'day': 365, 'week': 52, 'month': 12, 'year': 1}
FEATURES = ('monthly_price', 'currency', 'size_sqft', 'price_per_sqft', 'bedrooms', 'bathrooms', 'unit_label', 'unit_floor',
            'unit_level', 'photo_count', 'latitude', 'longitude')

UNIT_WORD = re.compile(r'(?:#|(?:apt|unit|ste|suite)\b\.?)\s*#?\s*', re.I)
ADDRESS_UNIT = re.compile(r'(?:#|\b(?:apt|unit|ste|suite)\b\.?)\s*#?\s*([^,#]+?)\s*(?:,|$)', re.I)
GARDEN = re.compile(r'(?:g|gdn|grdn|garden|bsmt|basement|ll|lower(?: level)?)(?:[ -]?(?:\d{1,2}|[nsewfr]|front|rear))?'
                    r'(?: (?:unit|level|apt|apartment))?', re.I)
PENTHOUSE = re.compile(r'ph[ -]?\d{0,2}|penthouse(?: \w+)?', re.I)
FLOOR = (re.compile(r'([1-9])[a-z]{1,2}(?: floor| fl)?', re.I), re.compile(r'([1-9])\d{2}'),
         re.compile(r'([1-9]\d?)(?:st|nd|rd|th) (?:floor|fl)', re.I))


def monthly(price, period):
    return price * PERIODS_PER_YEAR[period] / 12 if price is not None and period in PERIODS_PER_YEAR else None


def bare_label(label):
    return UNIT_WORD.sub('', label.strip(), count=1).strip() if label else ''


def label_floor(label):
    return next((int(match.group(1)) for pattern in FLOOR if (match := pattern.fullmatch(label))), None)


def unit_level(label, floor, stories):
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


def address_label(listing):
    match = ADDRESS_UNIT.search(listing.get('address') or listing.get('title') or '')
    return match.group(1) if match else None


def chosen_unit(units):
    priced = [unit for unit in units if monthly(unit.get('price'), unit.get('period')) is not None]
    if priced:
        return min(priced, key=lambda unit: monthly(unit['price'], unit['period']))
    return units[0] if len(units) == 1 else {}


def code_features(listing, facts):
    """The features of a listing, a stored listing object, given its site facts or None."""
    facts = facts or {}
    units = facts.get('units') or [{'price': listing.get('price'), 'period': listing.get('rental_period')}]
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
    }


def store_code_features(connection, listing_id):
    """Computes and stores the features of a listing's current content, from its facts when they are for that content."""
    row = connection.execute('''SELECT l.data, l.fingerprint, CASE WHEN r.fingerprint = l.fingerprint THEN r.features END AS features
        FROM listings l LEFT JOIN ranking r ON r.listing_id = l.id WHERE l.id = ?''', (listing_id,)).fetchone()
    facts = json.loads(row[2]).get('facts') if row[2] else None
    features = code_features(json.loads(row[0]), facts)
    connection.execute(f'''INSERT INTO code_features (listing_id, fingerprint, {", ".join(FEATURES)})
        VALUES (?, ?, {", ".join("?" for _ in FEATURES)}) ON CONFLICT(listing_id) DO UPDATE SET fingerprint = excluded.fingerprint,
        {", ".join(f"{name} = excluded.{name}" for name in FEATURES)}''', (listing_id, row[1], *(features[name] for name in FEATURES)))
