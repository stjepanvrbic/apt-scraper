"""Ranks listings from the features the model stored once per listing content, so a new ranking costs no model call.

The ranking keeps two keys in a listing's features, next to the search's facts and must_haves:

  profile      what the model saw, written once per content: a one-line summary, the terms of VOCABULARY that apply
               in each dimension, and the neighborhood
  preferences  the model's judgment of each stated preference the vocabulary cannot express: true when the listing
               has it, false when it rules it out, null when it does not say

A user note gets one reading: its sentiment and the vocabulary terms it liked or disliked. A stated like or dislike
gets one reading: the vocabulary terms that express it faithfully, so a listing has it when its profile has one of
them, or no terms and the reason the vocabulary does not express it well, so each listing is judged on it.

Favorites and positive notes make their listings positive examples, dismissals and negative notes negative ones.
A listing scores by its similarity to the closest examples across photos, description, amenities, location, price,
size and rooms, by how many of the traits the examples share it has, by its judged likes and dislikes, and by the
terms the notes liked or disliked.
"""

import math
from datetime import date

VOCABULARY = {
    'photos': (
        'modern finishes', 'classic vintage details', 'industrial loft', 'luxury finishes', 'dated finishes', 'newly renovated',
        'worn condition', 'hardwood floors', 'carpet', 'tile or vinyl floors', 'renovated kitchen', 'dated kitchen',
        'stainless steel appliances', 'kitchen island', 'small kitchen', 'large kitchen', 'white cabinets', 'dark cabinets',
        'updated bathroom', 'dated bathroom', 'bright natural light', 'dim rooms', 'large windows', 'high ceilings',
        'low ceilings', 'exposed brick', 'fireplace', 'bay windows', 'crown molding', 'spacious rooms', 'small rooms',
        'open layout', 'city view', 'lake view', 'balcony or terrace', 'private yard', 'rooftop deck', 'furnished',
        'high-rise building', 'mid-rise building', 'walk-up building', 'two-flat or house', 'courtyard building',
        'new construction', 'brick building',
    ),
    'description': (
        'quiet', 'luxury', 'vintage charm', 'newly renovated', 'recently updated', 'needs updating', 'garden unit',
        'top floor', 'ground floor', 'basement unit', 'corner unit', 'duplex', 'open layout', 'spacious', 'compact',
        'professionally managed', 'owner managed', 'utilities included', 'heat included', 'short lease available',
        'move-in specials', 'family friendly', 'student friendly',
    ),
    'amenities': (
        'in-unit laundry', 'laundry in building', 'dishwasher', 'central air', 'window air conditioning', 'garage parking',
        'outdoor parking', 'storage', 'elevator', 'doorman', 'fitness center', 'pool', 'rooftop deck', 'balcony or terrace',
        'private yard', 'shared yard', 'patio', 'bike storage', 'package lockers', 'pets allowed', 'dogs allowed',
        'cats allowed', 'no pets', 'furnished', 'walk-in closet', 'hardwood floors', 'fireplace',
    ),
    'location': (
        'near the lake', 'near a park', 'near a train station', 'near a bus line', 'near shops and restaurants',
        'nightlife nearby', 'quiet residential street', 'busy street', 'near a highway', 'near schools',
        'near a university', 'near a hospital', 'near grocery stores',
    ),
}
TERMS = frozenset(term for terms in VOCABULARY.values() for term in terms)
SENTIMENTS = ('positive', 'negative', 'mixed', 'neutral')
NOT_RANKED = {
    'dismissed': 'Dismissed, so not ranked.',
    'unavailable': 'No longer available, so not ranked.',
    'outside': 'Outside your criteria, so not ranked.',
}
UNANALYZED = 'Ranked by price, size and rooms only, because its photos and text could not be analyzed.'
SUMMARY_LIMIT = 200
REASON_LIMIT = 200
PERIODS_PER_YEAR = {'day': 365, 'week': 52, 'month': 12, 'year': 1}

WEIGHTS = {'photos': 1.0, 'description': 1.0, 'amenities': 1.0, 'location': 1.0, 'price': 1.0, 'size': 0.5, 'rooms': 0.5}
PRICE_SCALE = 0.2
SIZE_SCALE = 0.3
CLOSEST_EXAMPLES = 3
TRAIT_PRIOR = 2
TRAIT_WEIGHT = 1.0
LEARNED_WEIGHT = 1.0
PREFERENCE_WEIGHT = 0.15
NOTE_TERM_WEIGHT = 0.1
FAVORITE_BONUS = 0.5
NOTABLE = 0.05


def validate_profile(profile):
    keys = ('summary', *VOCABULARY, 'neighborhood')
    if not isinstance(profile, dict) or set(profile) != set(keys):
        raise ValueError(f'A profile has exactly {", ".join(keys)}.')
    summary = profile['summary']
    if not isinstance(summary, str) or not summary.strip() or len(summary) > SUMMARY_LIMIT or '\n' in summary:
        raise ValueError(f'The summary is one line of 1 to {SUMMARY_LIMIT} characters.')
    if profile['neighborhood'] is not None and (not isinstance(profile['neighborhood'], str) or not 0 < len(profile['neighborhood'].strip()) <= 100):
        raise ValueError('The neighborhood is its name, or null when the listing does not show it.')
    for dimension, terms in VOCABULARY.items():
        if not isinstance(profile[dimension], list):
            raise ValueError(f'{dimension} is an array of terms.')
        for term in profile[dimension]:
            if term not in terms:
                raise ValueError(f'"{term}" is not a {dimension} term. The {dimension} terms are: {", ".join(terms)}.')


def validate_reading(reading):
    if not isinstance(reading, dict) or set(reading) != {'sentiment', 'likes', 'dislikes'}:
        raise ValueError('A note reading has exactly sentiment, likes and dislikes.')
    if reading['sentiment'] not in SENTIMENTS:
        raise ValueError(f'The sentiment is one of {", ".join(SENTIMENTS)}.')
    for key in ('likes', 'dislikes'):
        if not isinstance(reading[key], list):
            raise ValueError(f'{key} is an array of vocabulary terms.')
        for term in reading[key]:
            if term not in TERMS:
                raise ValueError(f'"{term}" is not a vocabulary term.')


def validate_preference_reading(reading):
    if not isinstance(reading, dict) or set(reading) != {'terms', 'reason'} or not isinstance(reading['terms'], list):
        raise ValueError('A preference reading has exactly terms, an array, and reason.')
    for term in reading['terms']:
        if term not in TERMS:
            raise ValueError(f'"{term}" is not a vocabulary term.')
    if reading['terms'] and reading['reason'] is not None:
        raise ValueError('A preference the vocabulary expresses has no reason. Give the reason only with no terms.')
    if not reading['terms'] and not (isinstance(reading['reason'], str) and 0 < len(reading['reason'].strip()) <= 300):
        raise ValueError('A preference read with no terms needs the reason, in at most 300 characters.')


def validate_preferences(judgments):
    if not isinstance(judgments, dict):
        raise ValueError('Preference judgments are an object of phrases.')
    for phrase, value in judgments.items():
        if value not in (True, False, None) or isinstance(value, int) and not isinstance(value, bool):
            raise ValueError(f'The judgment of "{phrase}" must be true, false or null.')


def current_features(listing):
    return listing['features'] if listing.get('analysis_current') else None


def rankable(listing, unanalyzable=frozenset()):
    """Shown in Rankings, with a profile or given up on by the analysis."""
    return bool(listing['matches_criteria'] and listing['status'] == 'available' and not listing['dismissed']
                and ((current_features(listing) or {}).get('profile') or listing['id'] in unanalyzable))


def unit_terms(listing):
    """The cheapest unit's monthly price, size and bedrooms, from the search's facts or the listing itself."""
    facts = (current_features(listing) or {}).get('facts') or {}
    units = [unit for unit in facts.get('units') or [] if unit.get('price') and unit.get('period') in PERIODS_PER_YEAR]
    if units:
        unit = min(units, key=lambda unit: unit['price'] * PERIODS_PER_YEAR[unit['period']])
        return unit['price'] * PERIODS_PER_YEAR[unit['period']] / 12, unit.get('size_sqft'), unit.get('bedrooms')
    price, period = listing.get('price'), listing.get('rental_period')
    return (price * PERIODS_PER_YEAR[period] / 12 if price and period in PERIODS_PER_YEAR else None), None, None


class Listing:
    def __init__(self, listing):
        """A listing without a profile, which the analysis gave up on, is known only by its price, size and rooms."""
        features = current_features(listing) or {}
        profile = features.get('profile')
        self.profiled = profile is not None
        profile = profile or {'summary': UNANALYZED, 'neighborhood': None, **{dimension: [] for dimension in VOCABULARY}}
        self.id = listing['id']
        self.label = (listing.get('address') or listing.get('title') or listing['id']).split(',')[0][:60]
        self.summary = profile['summary'].strip()
        self.neighborhood = profile['neighborhood'].strip() if profile['neighborhood'] else None
        self.terms = {dimension: set(profile[dimension]) for dimension in VOCABULARY}
        if self.neighborhood:
            self.terms['location'].add('neighborhood: ' + self.neighborhood.lower())
        self.price, self.size, self.bedrooms = unit_terms(listing)
        self.preferences = features.get('preferences') or {}
        self.all_terms = set().union(*(self.terms[dimension] for dimension in VOCABULARY))


def closeness(first, second, scale):
    if not first or not second:
        return None
    return math.exp(-(math.log(first / second) / scale) ** 2)


class Model:
    def __init__(self, analyzed):
        self.size = len(analyzed)
        self.counts = {dimension: {} for dimension in VOCABULARY}
        for listing in analyzed:
            for dimension in VOCABULARY:
                for term in listing.terms[dimension]:
                    self.counts[dimension][term] = self.counts[dimension].get(term, 0) + 1
        self.weights = {dimension: {term: math.log(1 + self.size / count) for term, count in counts.items()}
                        for dimension, counts in self.counts.items()}

    def traits(self, examples):
        """The terms at least two examples share and that are more common among them than among all listings, each
        valued by how much more common (its lift, with the examples' share drawn toward the overall share) times
        the share of examples that have it."""
        total = sum(weight for _, weight in examples)
        shared = {}
        for example, weight in examples:
            for dimension in VOCABULARY:
                for term in example.terms[dimension]:
                    shared[dimension, term] = shared.get((dimension, term), 0) + weight
        traits = {}
        for (dimension, term), count in shared.items():
            overall = self.counts[dimension][term] / self.size
            lift = math.log((count + TRAIT_PRIOR * overall) / (total + TRAIT_PRIOR) / overall)
            if count >= 2 and lift > 0:
                traits[dimension, term] = lift * count / total
        return traits

    def term_similarity(self, dimension, first, second):
        union = first.terms[dimension] | second.terms[dimension]
        if not union:
            return None
        weights = self.weights[dimension]
        return sum(weights[term] for term in first.terms[dimension] & second.terms[dimension]) / sum(weights[term] for term in union)

    def similarity(self, first, second):
        parts = {dimension: self.term_similarity(dimension, first, second) if first.profiled and second.profiled else None
                 for dimension in VOCABULARY}
        parts['price'] = closeness(first.price, second.price, PRICE_SCALE)
        parts['size'] = closeness(first.size, second.size, SIZE_SCALE)
        if first.bedrooms is not None and second.bedrooms is not None:
            parts['rooms'] = max(0.0, 1 - abs(first.bedrooms - second.bedrooms) / 2)
        known = {dimension: value for dimension, value in parts.items() if value is not None}
        if not known:
            return 0.0
        return sum(WEIGHTS[dimension] * value for dimension, value in known.items()) / sum(WEIGHTS[dimension] for dimension in known)

    def shared(self, first, second):
        terms = sorted(((self.weights[dimension][term], term) for dimension in VOCABULARY
                        for term in first.terms[dimension] & second.terms[dimension]), reverse=True)
        names = []
        for _, term in terms:
            name = term.removeprefix('neighborhood: ')
            name = first.neighborhood if name != term and first.neighborhood else name
            if name not in names:
                names.append(name)
        if (closeness(first.price, second.price, PRICE_SCALE) or 0) > 0.8:
            names.append('similar price')
        return names[:3]


def trait_share(listing, traits):
    """The share of the traits' value the listing has, with its traits from the most to the least valued."""
    present = sorted(((value, term) for (dimension, term), value in traits.items() if term in listing.terms[dimension]), reverse=True)
    names = [listing.neighborhood if term.startswith('neighborhood: ') and listing.neighborhood else term for _, term in present]
    return sum(value for value, _ in present) / sum(traits.values()) if traits else 0.0, list(dict.fromkeys(names))


def affinity(model, listing, examples):
    """The mean similarity to the closest examples, an example of weight 2 counting twice."""
    scored = sorted(((model.similarity(listing, example), example) for example, weight in examples if example.id != listing.id
                     for _ in range(weight)), key=lambda pair: pair[0], reverse=True)[:CLOSEST_EXAMPLES]
    if not scored:
        return 0.0, None
    return sum(value for value, _ in scored) / len(scored), scored[0][1]


def phrase(text):
    return text[0].lower() + text[1:] if text[1:2].islower() else text


def listed(items):
    items = list(items)
    return ', '.join(items[:-1]) + ' and ' + items[-1] if len(items) > 1 else ''.join(items)


def reason_from(clauses, summary):
    chosen = [text for strength, text in sorted(clauses, key=lambda clause: clause[0], reverse=True) if strength > 0][:2]
    while chosen:
        text = ' '.join(clause[0].upper() + clause[1:] + '.' for clause in chosen)
        if len(text) <= REASON_LIMIT:
            return text
        chosen.pop()
    return summary if summary.endswith('.') else summary + '.'


def has(listing, phrase, reading):
    """Whether the listing has a stated preference: one of the terms it maps to, or its own judgment when the
    vocabulary does not express it well. A preference not read yet does not count."""
    if reading is None:
        return False
    if reading['terms']:
        return bool(listing.all_terms & set(reading['terms']))
    return listing.preferences.get(phrase) is True


def rank(listings, preferences, readings, unanalyzable=frozenset(), preference_readings=None):
    """Returns ({id: (rank, reason)} for every listing shown in Rankings, {id: reason} for the listings left out of it).
    A listing that waits for its profile is in neither. A shown listing in unanalyzable, which the analysis gave up on,
    is ranked by its price, size and rooms. preference_readings maps stated phrases to their readings."""
    preference_readings = preference_readings or {}
    analyzed = {listing['id']: Listing(listing) for listing in listings if (current_features(listing) or {}).get('profile')}
    model = Model(list(analyzed.values()))
    analyzed.update({listing['id']: Listing(listing) for listing in listings if listing['id'] not in analyzed and rankable(listing, unanalyzable)})
    weights, term_weights, praised = {}, {}, set()
    for listing in listings:
        weight = int(listing['favorite']) - int(listing['dismissed'])
        for note in listing.get('notes', []):
            reading = readings.get(note['id'])
            if note['role'] != 'user' or not reading:
                continue
            weight += {'positive': 1, 'negative': -1}.get(reading['sentiment'], 0)
            if reading['sentiment'] == 'positive' and not listing['favorite']:
                praised.add(listing['id'])
            for term in reading['likes']:
                term_weights[term] = term_weights.get(term, 0) + 1
            for term in reading['dislikes']:
                term_weights[term] = term_weights.get(term, 0) - 1
        if weight and listing['id'] in analyzed:
            weights[listing['id']] = max(-2, min(2, weight))
    positives = [(analyzed[identifier], weight) for identifier, weight in weights.items() if weight > 0]
    negatives = [(analyzed[identifier], -weight) for identifier, weight in weights.items() if weight < 0]
    likes, dislikes = preferences.get('likes', []), preferences.get('dislikes', [])
    shown = [listing for listing in listings if rankable(listing, unanalyzable)]
    closest = {listing['id']: (affinity(model, analyzed[listing['id']], positives), affinity(model, analyzed[listing['id']], negatives))
               for listing in shown}
    baseline = [sum(closest[identifier][side][0] for identifier in closest) / max(1, len(closest)) for side in (0, 1)]
    liked_traits, disliked_traits = model.traits(positives), model.traits(negatives)

    scored = []
    for listing in shown:
        item = analyzed[listing['id']]
        (positive, nearest_positive), (negative, nearest_negative) = closest[item.id]
        liked = [text for text in likes if has(item, text, preference_readings.get(text))]
        disliked = [text for text in dislikes if has(item, text, preference_readings.get(text))]
        noted = {term: max(-2, min(2, weight)) for term, weight in term_weights.items() if term in item.all_terms and weight}
        (shares_liked, liked_names), (shares_disliked, disliked_names) = trait_share(item, liked_traits), trait_share(item, disliked_traits)
        score = (LEARNED_WEIGHT * (positive - negative) + TRAIT_WEIGHT * (shares_liked - shares_disliked)
                 + PREFERENCE_WEIGHT * (len(liked) - len(disliked))
                 + NOTE_TERM_WEIGHT * sum(noted.values()) + FAVORITE_BONUS * listing['favorite'])
        clauses = [(10, 'one of your favorites')] if listing['favorite'] else []
        if nearest_positive and positive - baseline[0] > NOTABLE:
            source = 'your notes on' if nearest_positive.id in praised else 'your favorite at'
            clauses.append((LEARNED_WEIGHT * (positive - baseline[0]),
                            f'like {source} {nearest_positive.label}: {listed(model.shared(item, nearest_positive)) or "similar overall"}'))
        if nearest_negative and negative - baseline[1] > NOTABLE:
            clauses.append((LEARNED_WEIGHT * (negative - baseline[1]),
                            f'resembles {nearest_negative.label}, which you turned down: {listed(model.shared(item, nearest_negative)) or "similar overall"}'))
        if shares_liked > NOTABLE:
            clauses.append((TRAIT_WEIGHT * shares_liked, f'has {listed(liked_names[:3])}, like your favorites'))
        if shares_disliked > NOTABLE:
            clauses.append((TRAIT_WEIGHT * shares_disliked, f'has {listed(disliked_names[:3])}, like the listings you turned down'))
        if liked:
            clauses.append((PREFERENCE_WEIGHT * len(liked), 'has ' + listed(map(phrase, liked))))
        if disliked:
            clauses.append((PREFERENCE_WEIGHT * len(disliked), 'has ' + listed(map(phrase, disliked)) + ', which you dislike'))
        if any(weight > 0 for weight in noted.values()):
            clauses.append((NOTE_TERM_WEIGHT * sum(weight for weight in noted.values() if weight > 0),
                            'has ' + listed(term for term, weight in noted.items() if weight > 0) + ', which your notes liked'))
        if any(weight < 0 for weight in noted.values()):
            clauses.append((-NOTE_TERM_WEIGHT * sum(weight for weight in noted.values() if weight < 0),
                            'has ' + listed(term for term, weight in noted.items() if weight < 0) + ', which your notes disliked'))
        scored.append((-score, -date.fromisoformat(listing['first_seen']).toordinal(), listing['id'], reason_from(clauses, item.summary) if item.profiled else UNANALYZED))
    ranked = {identifier: (position, reason) for position, (_, _, identifier, reason) in enumerate(sorted(scored), start=1)}

    unranked = {}
    for listing in listings:
        if listing['dismissed']:
            unranked[listing['id']] = NOT_RANKED['dismissed']
        elif listing['status'] != 'available':
            unranked[listing['id']] = NOT_RANKED['unavailable']
        elif not listing['matches_criteria']:
            unranked[listing['id']] = NOT_RANKED['outside']
    return ranked, unranked
