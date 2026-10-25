"""Decides whether a listing meets the user's criteria from the features stored with it.

Features are a JSON object saved against the listing's content fingerprint:

  facts       written by the search from the listing site's structured data:
              areas     the criteria areas whose search found the listing
              currency  three-letter code of the unit prices
              units     one entry per rentable unit, each with price, period (day, week, month, year
                        or total), bedrooms, size_sqft and available_on (YYYY-MM-DD), any of them null
  must_haves  the model's judgments, by phrase: true when the listing meets it, false when the listing
              rules it out, null when the listing does not say. The phrases are the must-haves in
              criteria.other, and for a listing without facts, such as one imported by hand, also one
              phrase per criterion, from criterion_phrases.

A fact the listing does not state never puts it outside. A building is inside when one unit meets every
criterion. Changed criteria are judged from stored features, so only phrases the model has not judged
yet need a model call.
"""

INSIDE = 'inside'
OUTSIDE = 'outside'
PENDING = 'pending'

SQUARE_FEET_PER_SQUARE_METER = 10.7639
PERIODS_PER_YEAR = {'day': 365, 'week': 52, 'month': 12, 'year': 1}


def within(value, limits, scale=1):
    if value is None:
        return True
    low, high = limits.get('min'), limits.get('max')
    return (low is None or value >= low * scale) and (high is None or value <= high * scale)


def price_within(unit, currency, limits):
    price, period = unit.get('price'), unit.get('period')
    if price is None or limits.get('currency') != currency or period not in PERIODS_PER_YEAR or limits.get('period') not in PERIODS_PER_YEAR:
        return True
    return within(price * PERIODS_PER_YEAR[period] / PERIODS_PER_YEAR[limits['period']], limits)


def unit_meets(unit, currency, criteria):
    size = criteria.get('size', {})
    move_in = criteria.get('move_in')
    return (price_within(unit, currency, criteria.get('price', {}))
            and within(unit.get('bedrooms'), criteria.get('rooms', {}))
            and within(unit.get('size_sqft'), size, SQUARE_FEET_PER_SQUARE_METER if size.get('unit') == 'm2' else 1)
            and (move_in is None or unit.get('available_on') is None or unit['available_on'] <= move_in))


def facts_meet(criteria, facts):
    areas = facts.get('areas') or []
    if areas and not set(areas) & set(criteria.get('location', [])):
        return False
    units = facts.get('units') or []
    return not units or any(unit_meets(unit, facts.get('currency'), criteria) for unit in units)


def amount(value):
    return f'{value:,g}' if value < 1e6 else f'{value:,.0f}'


def span(limits):
    low, high = limits.get('min'), limits.get('max')
    if low is not None and high is not None:
        return f'from {amount(low)} to {amount(high)}'
    return f'at least {amount(low)}' if low is not None else f'at most {amount(high)}' if high is not None else None


def criterion_phrases(criteria):
    """The criteria as phrases the model judges for a listing that has no facts from a listing site."""
    phrases = ['In one of these areas: ' + '; '.join(criteria['location'])] if criteria.get('location') else []
    price, size, rooms = criteria.get('price', {}), criteria.get('size', {}), criteria.get('rooms', {})
    if span(price):
        phrases.append(f'Price {span(price)} {price["currency"]} ' + ('in total' if price['period'] == 'total' else f'a {price["period"]}'))
    if span(size):
        phrases.append(f'Size {span(size)} {size["unit"]}')
    if span(rooms):
        phrases.append(f'Rooms {span(rooms)}')
    if criteria.get('move_in'):
        phrases.append(f'Can be moved into by {criteria["move_in"]}')
    return phrases


def required(criteria, features):
    facts = (features or {}).get('facts')
    return ([] if facts is not None else criterion_phrases(criteria)) + list(criteria.get('other', []))


def unjudged(criteria, features):
    judged = (features or {}).get('must_haves', {})
    return [phrase for phrase in required(criteria, features) if phrase not in judged]


def evaluate(criteria, features):
    facts = (features or {}).get('facts')
    if facts is not None and not facts_meet(criteria, facts):
        return OUTSIDE
    judged = (features or {}).get('must_haves', {})
    if any(judged.get(phrase) is False for phrase in required(criteria, features)):
        return OUTSIDE
    return PENDING if unjudged(criteria, features) else INSIDE
