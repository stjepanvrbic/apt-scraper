"""Decides whether a listing meets the user's criteria from the features stored with it.

Features are a JSON object saved against the listing's content fingerprint:

  facts       written by the search from the listing site's structured data:
              areas     the criteria areas whose search found the listing
              currency  three-letter code of the unit prices
              latitude, longitude  of the listing, both null when the site gives none
              stories   floors in the building
              units     one entry per rentable unit, each with price, period (day, week, month, year
                        or total), bedrooms, bathrooms, size_sqft, available_on (YYYY-MM-DD), label (the
                        unit's number or name, such as "Unit G2") and floor, any of them null
  must_haves  the model's judgments, by phrase: true when the listing meets it, false when the listing
              rules it out, null when the listing does not say. The phrases are the must-haves in
              criteria.other, and for a listing without facts, such as one imported by hand, also one
              phrase per criterion, from criterion_phrases.

A fact the listing does not state never puts it outside. A building is inside when one unit meets every
criterion. Changed criteria are judged from stored features, so only phrases the model has not judged
yet need a model call.
"""

from typing import Literal, TypedDict

from shapes import Criteria, PriceLimit, Range, RentalPeriod

Verdict = Literal['inside', 'outside', 'pending']
INSIDE: Verdict = 'inside'
OUTSIDE: Verdict = 'outside'
PENDING: Verdict = 'pending'

SQUARE_FEET_PER_SQUARE_METER = 10.7639
PERIODS_PER_YEAR = {'day': 365, 'week': 52, 'month': 12, 'year': 1}


class Unit(TypedDict, total=False):
    price: int | float | None
    period: RentalPeriod
    bedrooms: int | float | None
    bathrooms: int | float | None
    size_sqft: int | float | None
    available_on: str | None
    label: str | None
    floor: int | None


class Facts(TypedDict, total=False):
    areas: list[str]
    currency: str
    latitude: float | None
    longitude: float | None
    stories: int | None
    units: list[Unit]


MustHaves = dict[str, bool | None]


class Features(TypedDict, total=False):
    facts: Facts
    must_haves: MustHaves


def within(value: int | float | None, limits: Range, scale: float = 1) -> bool:
    if value is None:
        return True
    low, high = limits.get('min'), limits.get('max')
    return (low is None or value >= low * scale) and (high is None or value <= high * scale)


def price_within(unit: Unit, currency: str | None, limits: PriceLimit) -> bool:
    price, period, limit_period = unit.get('price'), unit.get('period'), limits.get('period')
    if price is None or limits.get('currency') != currency or period not in PERIODS_PER_YEAR or limit_period not in PERIODS_PER_YEAR:
        return True
    return within(price * PERIODS_PER_YEAR[period] / PERIODS_PER_YEAR[limit_period], limits)


def unit_meets(unit: Unit, currency: str | None, criteria: Criteria) -> bool:
    size = criteria.get('size', {})
    move_in = criteria.get('move_in')
    available_on = unit.get('available_on')
    return (price_within(unit, currency, criteria.get('price', {}))
            and within(unit.get('bedrooms'), criteria.get('rooms', {}))
            and within(unit.get('size_sqft'), size, SQUARE_FEET_PER_SQUARE_METER if size.get('unit') == 'm2' else 1)
            and (move_in is None or available_on is None or available_on <= move_in))


def facts_meet(criteria: Criteria, facts: Facts) -> bool:
    areas = facts.get('areas') or []
    if areas and not set(areas) & set(criteria.get('location', [])):
        return False
    units = facts.get('units') or []
    return not units or any(unit_meets(unit, facts.get('currency'), criteria) for unit in units)


def amount(value: int | float) -> str:
    return f'{value:,g}' if value < 1e6 else f'{value:,.0f}'


def span(limits: Range) -> str | None:
    low, high = limits.get('min'), limits.get('max')
    if low is not None and high is not None:
        return f'from {amount(low)} to {amount(high)}'
    return f'at least {amount(low)}' if low is not None else f'at most {amount(high)}' if high is not None else None


def criterion_phrases(criteria: Criteria) -> list[str]:
    """The criteria as phrases the model judges for a listing that has no facts from a listing site."""
    location = criteria.get('location')
    phrases = ['In one of these areas: ' + '; '.join(location)] if location else []
    price, size, rooms = criteria.get('price', {}), criteria.get('size', {}), criteria.get('rooms', {})
    if span(price):
        period = price['period']  # pyright: ignore[reportTypedDictNotRequiredAccess]  # the store saves a price limit only with its currency and period
        currency = price['currency']  # pyright: ignore[reportTypedDictNotRequiredAccess]  # the store saves a price limit only with its currency and period
        phrases.append(f'Price {span(price)} {currency} ' + ('in total' if period == 'total' else f'a {period}'))
    if span(size):
        unit = size['unit']  # pyright: ignore[reportTypedDictNotRequiredAccess]  # the store saves a size limit only with its unit
        phrases.append(f'Size {span(size)} {unit}')
    if span(rooms):
        phrases.append(f'Rooms {span(rooms)}')
    move_in = criteria.get('move_in')
    if move_in:
        phrases.append(f'Can be moved into by {move_in}')
    return phrases


def required(criteria: Criteria, features: Features | None) -> list[str]:
    facts = (features or {}).get('facts')
    return ([] if facts is not None else criterion_phrases(criteria)) + list(criteria.get('other', []))


def unjudged(criteria: Criteria, features: Features | None) -> list[str]:
    judged = (features or {}).get('must_haves', {})
    return [phrase for phrase in required(criteria, features) if phrase not in judged]


def evaluate(criteria: Criteria, features: Features | None) -> Verdict:
    facts = (features or {}).get('facts')
    if facts is not None and not facts_meet(criteria, facts):
        return OUTSIDE
    judged = (features or {}).get('must_haves', {})
    if any(judged.get(phrase) is False for phrase in required(criteria, features)):
        return OUTSIDE
    return PENDING if unjudged(criteria, features) else INSIDE
