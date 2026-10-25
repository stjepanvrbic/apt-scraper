"""The scoring model: a Bayesian logistic regression over the listings' feature records, fitted in code.

The features are the columns `design` builds from each listing's feature record (record.py), its code-derived
features (code_features.py) with its distances to the nearest rapid-transit station and to each place, the criteria
areas whose search found it, its nearness to the favorites and its judgments of the stated preferences no other
feature expresses. Numeric columns are centered and scaled to standard
deviation 0.5 and binary columns centered (Gelman et al. 2008, "A weakly informative default prior distribution for
logistic and other regression models"). A value the listing does not state is the column mean, so it counts neither
way.

The prior on each weight is Gaussian. Its mean is set by the stated likes and dislikes and the likes and dislikes
read from notes, through the features their readings name, and is zero elsewhere. Favorites, contact requests,
dismissals and note sentiments label their listings, every other ranked listing is a weak negative, and a pairwise
answer is a logistic term on the two listings' difference. Newton iterations find the posterior mode, and its Hessian
gives the Laplace posterior used for exploration and pairwise questions. The prior scale and the weak-negative weight
are chosen by the leave-one-out check.

A listing's score is the sum of its contributions w_j (x_j - mean_j), so every explanation is exact.

Most listings have few of the many tags, so the likelihood keeps tags and "not shown" indicators uncentered and
sparse. The intercept's prior is nearly flat, so centering a column only moves the intercept and leaves every other
weight and its posterior as they are.

Everything here is a pure function of its inputs, which ListingStore.ranking_inputs reads.
"""

import math
import random
from dataclasses import dataclass
from functools import cached_property
from math import sumprod as dot
from operator import mul
from statistics import fmean, median, pstdev

from code_features import distance_km
from record import CATEGORIES, RATINGS

STATED_WEIGHT = 1.0
NOTE_WEIGHT = 0.5
PRIOR_MEAN_LIMIT = 2.0
INTERCEPT_PRECISION = 1e-6
CONTACT_WEIGHT = 2.0
PRIOR_SCALES = (1.0, 0.5, 2.0)
UNLABELED_WEIGHTS = (0.05, 0.1)
DEFAULT_HYPERPARAMETERS = (PRIOR_SCALES[0], UNLABELED_WEIGHTS[0])
TUNING_FAVORITES = 3
IMPORTANCE = {'low': 0.25, 'medium': 0.5, 'high': 1.0}
LOCATION_BANDWIDTH_KM = 1.0
TOP = 20
EXPLORATION_RANKS = (7, 14)
QUESTION_CANDIDATES = 40
MIN_TAG_LISTINGS = 2
NEWTON_TOLERANCE = 1e-8
CHORD_START = 1e-2
HELD_OUT_TOLERANCE = 1e-6
HELD_OUT_ITERATIONS = 30
REASON_UP = 3
REASON_DOWN = 2
REASON_THRESHOLD = 0.05
REASON_LIMIT = 500
SINGLE_LABEL_WEIGHT = 0.25
SENTIMENTS = ('positive', 'negative', 'mixed', 'neutral')
DIRECTIONS = ('like', 'dislike')
CODE_NUMBERS = {
    'price': ('monthly_price', math.log),
    'size': ('size_sqft', math.log),
    'price_per_sqft': ('price_per_sqft', float),
    'bedrooms': ('bedrooms', float),
    'bathrooms': ('bathrooms', float),
    'photos': ('photo_count', float),
}
UNIT_LEVELS = {'garden': 'garden', 'garden or basement': 'garden', 'ground': 'ground', 'middle': 'middle', 'top': 'top'}
FLAG_WORDS = {
    'in_unit_laundry': 'in-unit laundry', 'laundry_in_building': 'laundry in the building', 'dishwasher': 'dishwasher',
    'air_conditioning': 'air conditioning', 'central_air': 'central air', 'parking': 'parking', 'pets_allowed': 'pets allowed',
    'utilities_included': 'utilities included', 'elevator': 'elevator', 'furnished': 'furnished',
}
CATEGORY_WORDS = {'floor': '{} floor', 'style': '{} style', 'outdoor_space': 'outdoor space: {}', 'building_type': '{} building'}


def sigmoid(value):
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1 + exponential)


def softplus(value):
    return max(value, 0) + math.log1p(math.exp(-abs(value)))


def cholesky(matrix):
    """The rows of the lower triangular L with L L^T = matrix, for a symmetric positive definite matrix, each up to its
    diagonal."""
    lower, prefixes = [], []
    for row, values in enumerate(matrix):
        current = []
        for column in range(row):
            current.append((values[column] - dot(current, prefixes[column])) / lower[column][column])
        prefixes.append(current[:])
        current.append(math.sqrt(values[row] - dot(current, current)))
        lower.append(current)
    return lower


@dataclass(frozen=True)
class Factor:
    """A Cholesky factor of a precision matrix: the rows of L up to the diagonal, and the columns of L below the
    diagonal in reverse order, for the backward solve."""
    lower: list
    reversed_tails: list

    @classmethod
    def of(cls, matrix):
        lower = cholesky(matrix)
        size = len(lower)
        return cls(lower, [[lower[row][column] for row in range(size - 1, column, -1)] for column in range(size)])

    def forward(self, vector):
        """L^-1 vector."""
        result = []
        for index, row in enumerate(self.prefixes):
            result.append((vector[index] - dot(row, result)) / self.lower[index][index])
        return result

    @cached_property
    def prefixes(self):
        return [row[:-1] for row in self.lower]

    def backward(self, vector):
        """L^-T vector."""
        reversed_result = []
        for index in range(len(vector) - 1, -1, -1):
            reversed_result.append((vector[index] - dot(self.reversed_tails[index], reversed_result)) / self.lower[index][index])
        return reversed_result[::-1]

    def solve(self, vector):
        return self.backward(self.forward(vector))


@dataclass(frozen=True)
class Column:
    name: str
    numeric: bool
    sparse: bool
    mean: float
    scale: float


@dataclass(frozen=True)
class Row:
    """One row of the likelihood: head is the intercept's coefficient followed by the dense columns' values, positions
    and values the nonzero values of the sparse columns, each by its position among the sparse columns, in order."""
    head: list
    positions: tuple
    values: tuple

    @property
    def sparse(self):
        return zip(self.positions, self.values)


@dataclass(frozen=True)
class Design:
    """The model's view of the listings it learns from or ranks, sorted by id. The dense columns come first, then the
    sparse ones. rows[i][j] is listing i's centered and scaled value of columns[j], 0 where the listing does not state
    it, likelihood_rows[i] the same listing as the likelihood sees it, and raw[i] its stated values by column name."""
    ids: tuple
    listings: dict
    columns: tuple
    dense_count: int
    rows: tuple
    likelihood_rows: tuple
    raw: tuple
    prior_mean: tuple
    favorites: tuple
    coordinates: dict
    kernels: dict
    nearness_sums: tuple

    def index(self, name):
        return next((position for position, column in enumerate(self.columns) if column.name == name), None)

    @cached_property
    def rows_without(self):
        """The likelihood's listing rows with the favorites' nearness computed without each favorite, by favorite."""
        column = self.index('near_favorites')
        if column is None:
            return {favorite: self.likelihood_rows for favorite in self.favorites}
        center, scale = self.columns[column].mean, self.columns[column].scale
        rows_without = {}
        for favorite in self.favorites:
            rows = list(self.likelihood_rows)
            for index, value in enumerate(self.kernels.get(favorite, ())):
                identifier = self.ids[index]
                if identifier == favorite or identifier not in self.coordinates:
                    continue
                total, count = self.nearness_sums[index]
                head = list(rows[index].head)
                head[column + 1] = ((total - value) / (count - 1) - center) / scale if count > 1 else 0.0
                rows[index] = Row(head, rows[index].positions, rows[index].values)
            rows_without[favorite] = rows
        return rows_without


@dataclass(frozen=True)
class Observations:
    """The rows the likelihood sees: one per labeled or weakly negative listing and one per pairwise answer, with its
    label and weight. listings[k] is the listing of row k, None for a pair, and pairs[k] the pair of row k. holders
    has, for each sparse column that some row has, the rows that have it and their values."""
    rows: list
    labels: list
    weights: list
    listings: list
    pairs: list
    holders: dict

    @cached_property
    def head_columns(self):
        return [list(column) for column in zip(*(row.head for row in self.rows))]


def observed(rows, labels, weights, listings, pairs):
    holders = {}
    for index, row in enumerate(rows):
        for position, value in row.sparse:
            holder_rows, values = holders.setdefault(position, ([], []))
            holder_rows.append(index)
            values.append(value)
    return Observations(rows, labels, weights, listings, pairs, holders)


@dataclass(frozen=True)
class Fit:
    weights: list
    factor: Factor
    curvature: list


def rating_words(name):
    return name.replace('_', ' ')


def stated_phrases(settings):
    preferences = settings.get('preferences', {})
    return [(kind, phrase) for kind, key in (('like', 'likes'), ('dislike', 'dislikes')) for phrase in preferences.get(key, [])]


def expressed(readings, kind, phrase):
    """Whether the reading of a stated preference names record features, so it needs no judgment per listing."""
    return bool(readings.get((kind, phrase)))


def reading_vocabulary(tags, areas, places=()):
    """Every feature a reading of a note or a stated preference may name, with what a like of it means."""
    vocabulary = {f'rating:{name}': f'toward {high} (5), away from {low} (1)' for name, (low, high) in RATINGS.items()}
    vocabulary.update({f'{name}:{value}': f'{name.replace("_", " ")} is {value}' for name, values in CATEGORIES.items() for value in values})
    vocabulary.update({f'unit_level:{level}': f'a {level} unit' for level in ('garden', 'ground', 'middle', 'top')})
    vocabulary.update({f'flag:{flag}': f'has {words}' for flag, words in FLAG_WORDS.items()})
    vocabulary.update({'price': 'a higher monthly price', 'size': 'a larger size', 'price_per_sqft': 'a higher price per square foot',
                       'bedrooms': 'more bedrooms', 'bathrooms': 'more bathrooms'})
    vocabulary.update({f'area:{area}': f'in {area}' for area in areas})
    vocabulary['station_distance'] = 'a longer distance to the nearest rapid-transit station'
    vocabulary.update({f'place:{place}': f'a longer distance to {place}' for place in places})
    vocabulary.update({f'tag:{tag}': f'has the trait or concern "{tag}"' for tag in tags})
    return vocabulary


def validate_preference_reading(features, vocabulary):
    """A preference reading: the record features the preference names, each with the direction a listing should
    move on it, or none when no feature expresses it."""
    if not isinstance(features, list):
        raise ValueError('features must be an array of {"feature", "direction"}, empty when no feature expresses the preference.')
    named = {}
    for item in features:
        if not isinstance(item, dict) or set(item) != {'feature', 'direction'}:
            raise ValueError('Each feature of a reading is {"feature", "direction"}.')
        if item['feature'] not in vocabulary:
            raise ValueError(f'"{item["feature"]}" is not a feature. Name features from the vocabulary.')
        if item['direction'] not in DIRECTIONS:
            raise ValueError(f'The direction of {item["feature"]} is like or dislike.')
        if named.setdefault(item['feature'], item['direction']) != item['direction']:
            raise ValueError(f'{item["feature"]} is named twice with different directions.')
    return [{'feature': feature, 'direction': direction} for feature, direction in named.items()]


def validate_note_reading(reading, vocabulary):
    """A note reading: the note's sentiment about its listing and the features it praises and criticizes."""
    if not isinstance(reading, dict) or set(reading) != {'sentiment', 'likes', 'dislikes'}:
        raise ValueError('A note reading is {"sentiment", "likes", "dislikes"}.')
    if reading['sentiment'] not in SENTIMENTS:
        raise ValueError(f'sentiment is {", ".join(SENTIMENTS)}.')
    for key in ('likes', 'dislikes'):
        if not isinstance(reading[key], list) or not all(isinstance(name, str) for name in reading[key]):
            raise ValueError(f'{key} must be an array of feature names.')
        unknown = [name for name in reading[key] if name not in vocabulary]
        if unknown:
            raise ValueError(f'{", ".join(unknown)} {"is" if len(unknown) == 1 else "are"} not in the vocabulary.')
    if set(reading['likes']) & set(reading['dislikes']):
        raise ValueError('A feature is either liked or disliked in one note.')
    return {'sentiment': reading['sentiment'], 'likes': list(dict.fromkeys(reading['likes'])), 'dislikes': list(dict.fromkeys(reading['dislikes']))}


def kernel(first, second):
    return math.exp(-(distance_km(first, second) / LOCATION_BANDWIDTH_KM) ** 2 / 2)


def nearness_kernels(ids, coordinates, favorites):
    """For each favorite with coordinates, its kernel at each listing's location, 0 at its own and at a listing
    without coordinates. A listing's nearness to the favorites is the mean kernel of the favorites other than itself,
    a smooth density rather than area counts, because area boundaries split homes a short walk apart (Zillow,
    "Personalized Location Preference for Home Recommendations", 2018). It is a mean rather than a sum because a
    favorite has one favorite fewer to be near than any other listing."""
    return {favorite: tuple(kernel(coordinates[identifier], coordinates[favorite]) if identifier in coordinates and identifier != favorite else 0.0
                            for identifier in ids)
            for favorite in favorites if favorite in coordinates}


def raw_values(listing, phrases, areas, density):
    """The listing's stated value of each column it has, by column name, before centering. Ratings are shrunk toward
    the mean by their confidence later, so they are kept as (value, confidence)."""
    values = {}
    record = listing['record']
    if record is not None:
        for name in RATINGS:
            rating = record['ratings'][name]
            values[f'rating_not_shown:{name}'] = float(rating == 'not shown')
            if rating != 'not shown':
                values[f'rating:{name}'] = (rating['value'], rating['confidence'])
        for name, choices in CATEGORIES.items():
            if record[name] != 'not shown':
                values.update({f'{name}:{value}': float(record[name] == value) for value in choices})
        for flag, value in record['flags'].items():
            if value is not None:
                values[f'flag:{flag}'] = float(value)
        for tag in {*record['traits'], *record['concerns']}:
            values[f'tag:{tag}'] = 1.0
    level = UNIT_LEVELS.get(listing['code'].get('unit_level') or (record['unit_level']['value'] if record else '') or '')
    if level:
        values.update({f'unit_level:{value}': float(level == value) for value in ('garden', 'ground', 'middle', 'top')})
    for name, (field, transform) in CODE_NUMBERS.items():
        value = listing['code'].get(field)
        if value is not None and (value > 0 or transform is float):
            values[name] = transform(value)
    if listing['areas'] is not None:
        values.update({f'area:{area}': float(area in listing['areas']) for area in areas})
    if density is not None:
        values['near_favorites'] = density
    if listing['code'].get('station_distance_km') is not None:
        values['station_distance'] = listing['code']['station_distance_km']
    for place, distance in (listing['code'].get('place_distances_km') or {}).items():
        values[f'place:{place}'] = distance
    for _, phrase in phrases:
        verdict = (listing['judgments'].get(phrase) or {}).get('verdict')
        if verdict in ('has', 'rules out'):
            values[f'preference:{phrase}'] = float(verdict == 'has')
    return values


def prior_means(inputs, phrases):
    """The starting weight of each feature the stated preferences and the note readings name, by column name."""
    means = {}

    def add(name, amount):
        means[name] = max(-PRIOR_MEAN_LIMIT, min(PRIOR_MEAN_LIMIT, means.get(name, 0.0) + amount))

    readings = inputs['preference_readings']
    for kind, phrase in stated_phrases(inputs['settings']):
        sign = 1 if kind == 'like' else -1
        if (kind, phrase) in phrases:
            add(f'preference:{phrase}', sign * STATED_WEIGHT)
        for item in readings.get((kind, phrase)) or []:
            add(item['feature'], (1 if item['direction'] == 'like' else -1) * STATED_WEIGHT)
    for reading in inputs['note_readings'].values():
        for name in reading['likes']:
            add(name, NOTE_WEIGHT)
        for name in reading['dislikes']:
            add(name, -NOTE_WEIGHT)
    priorities = inputs['settings'].get('location_priorities', {})
    areas = priorities.get('areas', {})
    for area, importance in areas.items():
        add(f'area:{area}', IMPORTANCE[importance] * STATED_WEIGHT)
    if areas:
        add('near_favorites', max(IMPORTANCE[importance] for importance in areas.values()) * STATED_WEIGHT)
    if 'rapid_transit' in priorities:
        add('station_distance', -IMPORTANCE[priorities['rapid_transit']] * STATED_WEIGHT)
    if 'places' in priorities:
        for place in inputs['settings'].get('places', []):
            add(f'place:{place["name"]}', -IMPORTANCE[priorities['places']] * STATED_WEIGHT)
    return means


def in_fit(listing):
    return (listing['record'] is not None or listing['given_up']) and (listing['rankable'] or labeled(listing))


def labeled(listing):
    return bool(listing['favorite'] or listing['dismissed'] or listing['contact'] or listing['user_notes'] or listing['paired'])


def design(inputs):
    """The columns and rows of every listing the model learns from or ranks: those with a feature record of their
    current content, or given up by extraction, that are ranked or labeled."""
    listings = {listing['id']: listing for listing in sorted(inputs['listings'], key=lambda listing: listing['id']) if in_fit(listing)}
    ids = tuple(listings)
    settings = inputs['settings']
    areas = list(settings.get('criteria', {}).get('location', []))
    readings = inputs['preference_readings']
    phrases = [(kind, phrase) for kind, phrase in stated_phrases(settings) if not expressed(readings, kind, phrase)]
    coordinates = {identifier: (listing['code']['latitude'], listing['code']['longitude']) for identifier, listing in listings.items()
                   if listing['code'].get('latitude') is not None and listing['code'].get('longitude') is not None}
    favorites = tuple(identifier for identifier in ids if listings[identifier]['favorite'] and not listings[identifier]['dismissed'])
    kernels = nearness_kernels(ids, coordinates, favorites)
    nearness_sums = tuple((sum(values[index] for favorite, values in kernels.items() if favorite != identifier),
                           sum(favorite != identifier for favorite in kernels)) for index, identifier in enumerate(ids))
    raw = [raw_values(listings[identifier], phrases, areas, total / count if count and identifier in coordinates else None)
           for identifier, (total, count) in zip(ids, nearness_sums)]
    means = prior_means(inputs, phrases)
    tag_counts = {}
    for values in raw:
        for name in values:
            if name.startswith('tag:'):
                tag_counts[name] = tag_counts.get(name, 0) + 1
    names = sorted({name for values in raw for name in values
                    if not name.startswith('tag:') or tag_counts[name] >= MIN_TAG_LISTINGS or name in means})
    for identifier, values in zip(ids, raw):
        if listings[identifier]['record'] is not None:
            for name in names:
                if name.startswith('tag:'):
                    values.setdefault(name, 0.0)
    for name in names:
        if name.startswith('rating:'):
            known = [values[name][0] for values in raw if name in values]
            average = fmean(known)
            for values in raw:
                if name in values:
                    value, confidence = values[name]
                    values[name] = average + confidence * (value - average)
    columns, matrix = [], []
    for name in sorted(names, key=lambda name: (sparse_column(name), name)):
        known = [values[name] for values in raw if name in values]
        numeric = name.startswith(('rating:', 'place:')) or name in (*CODE_NUMBERS, 'near_favorites', 'station_distance')
        spread = pstdev(known)
        if spread == 0:
            continue
        average = fmean(known)
        scale = 2 * spread if numeric else 1.0
        columns.append(Column(name, numeric, sparse_column(name), average, scale))
        matrix.append([(values[name] - average) / scale if name in values else 0.0 for values in raw])
    rows = tuple(list(row) for row in zip(*matrix)) if matrix else tuple([] for _ in ids)
    dense_count = sum(not column.sparse for column in columns)
    likelihood_rows = tuple(sparse_row([1.0, *row[:dense_count]], {position: value + column.mean for position, (column, value)
                                                                   in enumerate(zip(columns[dense_count:], row[dense_count:]))})
                            for row in rows)
    return Design(ids, listings, tuple(columns), dense_count, rows, likelihood_rows, tuple(raw),
                  tuple(means.get(column.name, 0.0) for column in columns), favorites, coordinates, kernels, nearness_sums)


def sparse_row(head, sparse):
    nonzero = sorted((position, value) for position, value in sparse.items() if value)
    return Row(head, tuple(position for position, _ in nonzero), tuple(value for _, value in nonzero))


def sparse_column(name):
    return name.startswith(('tag:', 'rating_not_shown:'))


def label(listing, sentiment):
    """(label, weight) of a listing from the user's actions, or None when it has none. An explicit action outranks a
    note's sentiment."""
    if listing['dismissed']:
        return 0, 1.0
    if listing['contact']:
        return 1, CONTACT_WEIGHT
    if listing['favorite']:
        return 1, 1.0
    if sentiment == 'positive':
        return 1, 1.0
    if sentiment == 'negative':
        return 0, 1.0
    return None


def observations(model_design, inputs, unlabeled_weight, rows=None, leave_out=None, *, weak_labels=None):
    """The likelihood's rows: each listing's label, or a weak negative for a ranked listing without one, and each
    pairwise answer. leave_out is a listing whose labels and answers are left out, rows the listing rows to use."""
    rows = model_design.likelihood_rows if rows is None else rows
    position = {identifier: index for index, identifier in enumerate(model_design.ids)}
    result = Observations([], [], [], [], [], {})
    for index, identifier in enumerate(model_design.ids):
        if identifier == leave_out:
            continue
        listing = model_design.listings[identifier]
        labeled_as = label(listing, inputs['sentiments'].get(identifier))
        if labeled_as is None and weak_labels:
            labeled_as = weak_labels.get(identifier)
        if labeled_as is None and listing['rankable']:
            labeled_as = (0, unlabeled_weight)
        if labeled_as is not None:
            result.rows.append(rows[index])
            result.labels.append(labeled_as[0])
            result.weights.append(labeled_as[1])
            result.listings.append(identifier)
            result.pairs.append(None)
    for chosen, other in inputs['pairs']:
        if chosen in position and other in position and leave_out not in (chosen, other) and chosen != other:
            result.rows.append(difference(rows[position[chosen]], rows[position[other]]))
            result.labels.append(1)
            result.weights.append(1.0)
            result.listings.append(None)
            result.pairs.append((chosen, other))
    return observed(result.rows, result.labels, result.weights, result.listings, result.pairs)


def held_out_observations(model_design, data, favorite):
    """The full fit's rows with the favorite's rows weighing nothing and the favorites' nearness computed without it.
    The sparse values do not change, so the rows keep their holders."""
    rows = model_design.rows_without[favorite]
    position = {identifier: index for index, identifier in enumerate(model_design.ids)}
    changed = rows is not model_design.likelihood_rows
    result = [rows[position[listing]] if listing is not None else difference(rows[position[pair[0]]], rows[position[pair[1]]]) if changed else row
              for row, listing, pair in zip(data.rows, data.listings, data.pairs)]
    weights = [0.0 if listing == favorite or (pair and favorite in pair) else weight for weight, listing, pair in zip(data.weights, data.listings, data.pairs)]
    return Observations(result, data.labels, weights, data.listings, data.pairs, data.holders)


def difference(first, second):
    sparse = dict(first.sparse)
    for position, value in second.sparse:
        sparse[position] = sparse.get(position, 0.0) - value
    return sparse_row([0.0, *(left - right for left, right in zip(first.head[1:], second.head[1:]))], sparse)


def expand(row, size):
    """The row as a full vector of the weights' length."""
    vector = row.head + [0.0] * (size - len(row.head))
    for position, value in row.sparse:
        vector[len(row.head) + position] = value
    return vector


def prior(model_design, scale):
    return [0.0, *model_design.prior_mean], [INTERCEPT_PRECISION, *([1 / scale ** 2] * len(model_design.columns))]


def linear(row, head, sparse):
    return dot(row.head, head) + dot(row.values, map(sparse.__getitem__, row.positions))


def values_of(data, weights, dense_count):
    head, sparse = weights[:dense_count + 1], weights[dense_count + 1:]
    return [linear(row, head, sparse) for row in data.rows]


def objective(data, weights, mean, precision, dense_count):
    likelihood = sum(weight * (softplus(value) - label * value)
                     for weight, label, value in zip(data.weights, data.labels, values_of(data, weights, dense_count)))
    return likelihood + sum(scale * (weight - center) ** 2 for weight, center, scale in zip(weights, mean, precision)) / 2


def gradient(data, weights, mean, precision, dense_count):
    """The gradient of the negative log posterior and each row's curvature weight."""
    probabilities = [sigmoid(value) for value in values_of(data, weights, dense_count)]
    residuals = [weight * (probability - label) for weight, probability, label in zip(data.weights, probabilities, data.labels)]
    result = [dot(column, residuals) for column in data.head_columns] or [0.0] * (dense_count + 1)
    sparse = [0.0] * (len(weights) - dense_count - 1)
    for position, (rows, values) in data.holders.items():
        sparse[position] = dot(values, map(residuals.__getitem__, rows))
    result += sparse
    return ([value + scale * (weight - center) for value, weight, center, scale in zip(result, weights, mean, precision)],
            [weight * probability * (1 - probability) for weight, probability in zip(data.weights, probabilities)])


def hessian(data, curvature, precision, dense_count):
    """The Hessian of the negative log posterior: the head block from the head columns, the sparse columns' blocks
    from the rows that have them."""
    size = len(precision)
    head = dense_count + 1
    matrix = [[0.0] * size for _ in range(size)]
    columns = data.head_columns
    weighted = [list(map(mul, column, curvature)) for column in columns]
    for row in range(len(columns)):
        for column in range(row + 1):
            matrix[row][column] = matrix[column][row] = dot(weighted[row], columns[column])
    for index, row in enumerate(data.rows):
        for first, (position, value) in enumerate(row.sparse):
            coefficient = curvature[index] * value
            target = matrix[head + position]
            for other, other_value in zip(row.positions[:first + 1], row.values[:first + 1]):
                target[head + other] += coefficient * other_value
    for position, (rows, values) in data.holders.items():
        coefficients = [curvature[index] * value for index, value in zip(rows, values)]
        cross = [dot(coefficients, column) for column in zip(*(data.rows[index].head for index in rows))]
        matrix[head + position][:head] = cross
        for column, value in enumerate(cross):
            matrix[column][head + position] = value
    for row in range(head, size):
        for column in range(head, row):
            matrix[column][row] = matrix[row][column]
    for index in range(size):
        matrix[index][index] += precision[index]
    return matrix


def fit(data, mean, precision, dense_count, start=None, *, settle=True):
    """The posterior mode, and the Cholesky factor of the Hessian there, or without settle the Hessian of the last
    Newton iteration. Newton iterations with step halving run until a step is below CHORD_START, then the last
    Hessian serves the remaining steps."""
    weights = list(mean) if start is None else list(start)
    current = objective(data, weights, mean, precision, dense_count)
    factor, factor_curvature = None, None
    while True:
        direction, curvature = gradient(data, weights, mean, precision, dense_count)
        if factor is None:
            factor = Factor.of(hessian(data, curvature, precision, dense_count))
        step = factor.solve(direction)
        length = 1.0
        while True:
            candidate = [weight - length * change for weight, change in zip(weights, step)]
            value = objective(data, candidate, mean, precision, dense_count)
            if value <= current or length < 1e-10:
                break
            length /= 2
        weights, current = candidate, value
        size = max(map(abs, step), default=0) * length
        if factor_curvature is None:
            factor_curvature = curvature
        if size < NEWTON_TOLERANCE:
            return settled(data, weights, mean, precision, dense_count) if settle else Fit(weights, factor, factor_curvature)
        if size >= CHORD_START:
            factor, factor_curvature = None, None


def settled(data, weights, mean, precision, dense_count):
    """The fit at weights with the Cholesky factor of the Hessian there."""
    _, curvature = gradient(data, weights, mean, precision, dense_count)
    return Fit(weights, Factor.of(hessian(data, curvature, precision, dense_count)), curvature)


def scores(rows, weights, dense_count):
    """Each listing's score up to one constant shared by all listings, which leaves their order as it is."""
    head, sparse = [0.0, *weights[1:dense_count + 1]], weights[dense_count + 1:]
    return [linear(row, head, sparse) for row in rows]


def held_out_fit(model_design, inputs, full, data, unlabeled_weight, mean, precision, favorite, *, steps=None):
    """The fit without one favorite's labels and answers, warm-started from the full fit. Its steps use the full
    fit's Hessian with the left-out rows taken out by the Woodbury identity. With steps, it stops after that many,
    which with one is the usual approximate leave-one-out; otherwise it runs until the change is below
    HELD_OUT_TOLERANCE and finishes with Newton iterations if that takes longer than HELD_OUT_ITERATIONS."""
    dense_count = model_design.dense_count
    rows = model_design.rows_without[favorite]
    reduced = held_out_observations(model_design, data, favorite)
    removed = [index for index, (listing, pair) in enumerate(zip(data.listings, data.pairs)) if listing == favorite or (pair and favorite in pair)]
    updates = [[math.sqrt(full.curvature[index]) * value for value in expand(data.rows[index], len(mean))] for index in removed]
    solved = [full.factor.solve(update) for update in updates]
    inverse = invert([[(1.0 if row == column else 0.0) - dot(updates[row], solved[column]) for column in range(len(updates))]
                      for row in range(len(updates))])
    weights = list(full.weights)
    for _ in range(steps or HELD_OUT_ITERATIONS):
        direction, _ = gradient(reduced, weights, mean, precision, dense_count)
        step = full.factor.solve(direction)
        if updates:
            correction = [dot(row, [dot(update, step) for update in updates]) for row in inverse]
            step = [value + sum(solved[index][position] * correction[index] for index in range(len(updates))) for position, value in enumerate(step)]
        weights = [weight - change for weight, change in zip(weights, step)]
        if steps is None and max(map(abs, step), default=0) < HELD_OUT_TOLERANCE:
            return rows, weights
    return rows, weights if steps else fit(reduced, mean, precision, dense_count, weights).weights


def invert(matrix):
    """The inverse of a small square matrix by Gauss-Jordan elimination with partial pivoting."""
    size = len(matrix)
    augmented = [list(row) + [1.0 if row_index == column else 0.0 for column in range(size)] for row_index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        lead = augmented[column][column]
        augmented[column] = [value / lead for value in augmented[column]]
        for row in range(size):
            if row != column:
                factor = augmented[row][column]
                augmented[row] = [value - factor * base for value, base in zip(augmented[row], augmented[column])]
    return [row[size:] for row in augmented]


def held_out_scores(model_design, inputs, full, data, unlabeled_weight, mean, precision, *, steps=None):
    """Each listing's score when the model is fitted without one favorite, by favorite."""
    result = {}
    for favorite in model_design.favorites:
        rows, weights = held_out_fit(model_design, inputs, full, data, unlabeled_weight, mean, precision, favorite, steps=steps)
        result[favorite] = scores(rows, weights, model_design.dense_count)
    return result


def held_out_ranks(model_design, held_out):
    """Each favorite's rank among the ranked listings that are not favorites, from the scores of the fit without it."""
    favorites = set(model_design.favorites)
    others = [index for index, identifier in enumerate(model_design.ids)
              if identifier not in favorites and model_design.listings[identifier]['rankable']]
    ranks = {}
    for favorite, values in held_out.items():
        own = values[model_design.ids.index(favorite)]
        ranks[favorite] = 1 + sum(values[index] > own for index in others)
    return ranks


@dataclass(frozen=True)
class Model:
    design: Design
    fit: Fit
    scale: float
    unlabeled_weight: float
    held_out_scores: dict

    @cached_property
    def held_out(self):
        return held_out_ranks(self.design, self.held_out_scores)


def summary_of(held_out):
    values = list(held_out.values())
    return {'median': median(values) if values else None, 'mean': fmean(values) if values else None}


def fit_model(inputs, hyperparameters=None, *, weak_labels=None, warm_start=None):
    """The fitted scoring model with the given (prior scale, weak-negative weight). Without them, once there are
    TUNING_FAVORITES favorites, the leave-one-out check chooses them: first the weak-negative weight among
    UNLABELED_WEIGHTS at the first of PRIOR_SCALES, then the prior scale among PRIOR_SCALES at that weight, each time
    the one whose approximate check, one step from the full fit, gives the favorites the best mean held-out rank, the
    earlier on a tie. Before that, DEFAULT_HYPERPARAMETERS. The held-out ranks it reports come from converged fits.
    warm_start reuses a model fitted to the same inputs when only diagnostic labels change."""
    model_design = warm_start.design if warm_start else design(inputs)
    dense_count = model_design.dense_count
    tuning = hyperparameters is None and len(model_design.favorites) >= TUNING_FAVORITES
    fits, start = {}, [warm_start.fit.weights if warm_start else None]

    def candidate(scale, unlabeled_weight):
        if (scale, unlabeled_weight) not in fits:
            mean, precision = prior(model_design, scale)
            data = observations(model_design, inputs, unlabeled_weight, weak_labels=weak_labels)
            full = fit(data, mean, precision, dense_count, start[0], settle=not tuning)
            start[0] = full.weights
            check = summary_of(held_out_ranks(model_design, held_out_scores(model_design, inputs, full, data, unlabeled_weight, mean, precision, steps=1)))['mean'] if tuning else None
            fits[scale, unlabeled_weight] = (check, mean, precision, data, full)
        return fits[scale, unlabeled_weight]

    if tuning:
        unlabeled_weight = min(UNLABELED_WEIGHTS, key=lambda weight: candidate(PRIOR_SCALES[0], weight)[0])
        scale = min(PRIOR_SCALES, key=lambda value: candidate(value, unlabeled_weight)[0])
    else:
        scale, unlabeled_weight = hyperparameters or DEFAULT_HYPERPARAMETERS
    _, mean, precision, data, full = candidate(scale, unlabeled_weight)
    if tuning:
        full = settled(data, full.weights, mean, precision, dense_count)
    return Model(model_design, full, scale, unlabeled_weight, held_out_scores(model_design, inputs, full, data, unlabeled_weight, mean, precision))


def display(model_design, index, name):
    """How the listing's record states the value of a column, in words, or None when it does not state it."""
    listing, raw = model_design.listings[model_design.ids[index]], model_design.raw[index]
    record, code = listing['record'], listing['code']
    if name not in raw:
        return None
    kind, _, key = name.partition(':')
    if kind == 'rating':
        return f'{rating_words(key)} {record["ratings"][key]["value"]} of 5'
    if kind == 'rating_not_shown':
        return f'{rating_words(key)} not shown' if raw[name] else None
    if kind in CATEGORIES:
        return CATEGORY_WORDS[kind].format(key) if raw[name] else None
    if kind == 'unit_level':
        if not raw[name]:
            return None
        source = f'unit label {code["unit_label"]}' if code.get('unit_level') else 'read from photos and text'
        return f'{key} unit ({source})'
    if kind == 'flag':
        return FLAG_WORDS[key] if raw[name] else f'no {FLAG_WORDS[key]}'
    if kind == 'tag':
        return key if raw[name] else None
    if kind == 'area':
        return f'in {key}' if raw[name] else f'outside {key}'
    if kind == 'preference':
        return f'has "{key}"' if raw[name] else f'rules out "{key}"'
    if name == 'near_favorites':
        here = model_design.coordinates[model_design.ids[index]]
        distances = [distance_km(here, model_design.coordinates[favorite]) for favorite in model_design.favorites
                     if favorite in model_design.coordinates and favorite != model_design.ids[index]]
        return f'{min(distances):.1f} km from the nearest favorite' if distances else None
    if name == 'station_distance':
        return f'{raw[name]:.1f} km to the nearest station, {code["nearest_station"]}'
    if kind == 'place':
        return f'{raw[name]:.1f} km from {key}'
    currency = f' {code["currency"]}' if code.get('currency') else ''
    return {
        'price': lambda: f'{code["monthly_price"]:,.0f}{currency} a month',
        'size': lambda: f'{code["size_sqft"]:,.0f} sq ft',
        'price_per_sqft': lambda: f'{code["price_per_sqft"]:,.2f}{currency} per sq ft',
        'bedrooms': lambda: f'{code["bedrooms"]:g} bedrooms',
        'bathrooms': lambda: f'{code["bathrooms"]:g} bathrooms',
        'photos': lambda: f'{code["photo_count"]} photos',
    }[name]()


def title(listing):
    return (listing['title'] or listing['address'] or listing['id'])[:80]


def weighted_rows(model_design, weights):
    """Each listing's contributions, column by column."""
    features = weights[1:]
    return [list(map(mul, row, features)) for row in model_design.rows]


def most_similar_favorite(model_design, contributed, index):
    """The favorite nearest the listing under the learned weights: the one whose contributions differ least."""
    identifier = model_design.ids[index]
    candidates = [model_design.ids.index(favorite) for favorite in model_design.favorites if favorite != identifier]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda other: (math.dist(contributed[index], contributed[other]), model_design.ids[other]))
    return model_design.ids[nearest]


def explanation(model_design, contributed, index):
    """The full explanation of a listing's score: every feature's contribution, largest first, which add up to the
    score, and the most similar favorite."""
    parts = [(column.name, value) for column, value in zip(model_design.columns, contributed[index])]
    favorite = most_similar_favorite(model_design, contributed, index)
    return {
        'score': sum(value for _, value in parts),
        'contributions': [{'feature': name, 'value': display(model_design, index, name), 'contribution': value}
                          for name, value in sorted(parts, key=lambda part: (-abs(part[1]), part[0]))],
        'similar_favorite': {'id': favorite, 'title': title(model_design.listings[favorite])} if favorite else None,
    }


def feature_reason(contributions):
    """The part of a reason that names the features that moved the score most, as the listing's record states them."""
    cited = [part for part in contributions if part['value'] is not None and abs(part['contribution']) >= REASON_THRESHOLD]
    up = [part['value'] for part in cited if part['contribution'] > 0][:REASON_UP]
    down = [part['value'] for part in cited if part['contribution'] < 0][:REASON_DOWN]
    sentences = [f'For it: {", ".join(up)}.'] if up else []
    if down:
        sentences.append(f'Against it: {", ".join(down)}.')
    return ' '.join(sentences) or 'Close to the average listing.'


def compose_reason(features, *, exploration, code_only, similar_favorite):
    """A listing's one-line reason: its exploration mark, a note when it is ranked from code features only, the part
    that names its features, and its most similar favorite."""
    sentences = ['Shown to learn your taste.'] if exploration else []
    if code_only:
        sentences.append('Ranked from code features only, because extraction failed 3 times.')
    sentences.append(features)
    if similar_favorite:
        sentences.append(f'Most like your favorite {similar_favorite["title"]}.')
    return ' '.join(sentences)[:REASON_LIMIT]


def reason(model_design, details, index, *, exploration):
    """One line naming the features that moved the score most, as the listing's record states them, and the most
    similar favorite."""
    return compose_reason(feature_reason(details['contributions']), exploration=exploration,
                          code_only=model_design.listings[model_design.ids[index]]['record'] is None,
                          similar_favorite=details['similar_favorite'])


def explore(model_design, fit_result, order, seed):
    """Two listings for EXPLORATION_RANKS, by posterior sampling: each draw of the weights from the Laplace posterior
    picks the unlabeled ranked listing outside the mean's top that scores highest under it."""
    keep = TOP - len(EXPLORATION_RANKS)
    candidates = [index for index in order[keep:] if not labeled(model_design.listings[model_design.ids[index]])]
    generator = random.Random(seed)
    chosen = []
    for _ in EXPLORATION_RANKS:
        remaining = [index for index in candidates if index not in chosen]
        if not remaining:
            break
        draw = fit_result.factor.backward([generator.gauss(0, 1) for _ in fit_result.weights])
        values = scores(model_design.likelihood_rows, [weight + change for weight, change in zip(fit_result.weights, draw)], model_design.dense_count)
        chosen.append(max(remaining, key=lambda index: (values[index], model_design.ids[index])))
    return chosen


def ranked_order(model_design, weights):
    rankable = [index for index, identifier in enumerate(model_design.ids) if model_design.listings[identifier]['rankable']]
    values = scores(model_design.likelihood_rows, weights, model_design.dense_count)
    return sorted(rankable, key=lambda index: (-values[index], model_design.ids[index]))


def rank_listings(inputs, seed, *, model=None):
    """The rerank's result: each ranked listing's rank, reason, explanation and exploration mark, the reason each
    other listing shown in Rankings is not ranked, and the summary with each favorite's held-out rank.
    model, when supplied, is already fitted to these inputs."""
    model = model or fit_model(inputs)
    model_design, weights = model.design, model.fit.weights
    order = ranked_order(model_design, weights)
    exploring = explore(model_design, model.fit, order, seed) if len(order) > TOP else []
    rest = [index for index in order if index not in exploring]
    for slot, index in zip(EXPLORATION_RANKS, exploring):
        rest.insert(slot - 1, index)
    contributed = weighted_rows(model_design, weights)
    ranked = {}
    for position, index in enumerate(rest, 1):
        identifier = model_design.ids[index]
        details = explanation(model_design, contributed, index)
        ranked[identifier] = {'fingerprint': model_design.listings[identifier]['fingerprint'], 'rank': position,
                              'reason': reason(model_design, details, index, exploration=index in exploring),
                              'explanation': details, 'exploration': index in exploring}
    unranked = {listing['id']: {'fingerprint': listing['fingerprint'], 'reason': 'Not ranked yet: waiting for its feature record.'}
                for listing in inputs['listings'] if listing['rankable'] and listing['id'] not in ranked}
    held_out = [{'id': identifier, 'title': title(model_design.listings[identifier]), 'rank': rank} for identifier, rank in model.held_out.items()]
    values = scores(model_design.likelihood_rows, weights, model_design.dense_count)
    position = {identifier: index for index, identifier in enumerate(model_design.ids)}
    return {'ranked': ranked, 'unranked': unranked, 'check': {
        'order': [model_design.ids[index] for index in rest], 'exploration': [model_design.ids[index] for index in exploring],
        'scores': [values[index] for index in rest],
        'held_out': {favorite: {'own': held[position[favorite]], 'scores': [held[index] for index in rest]}
                     for favorite, held in model.held_out_scores.items()},
    }, 'summary': {
        'ranked': len(ranked), 'waiting_for_features': len(unranked),
        'favorites': len(model_design.favorites), 'held_out': held_out, **{f'held_out_{key}': value for key, value in summary_of(model.held_out).items()},
        'exploration': [model_design.ids[index] for index in exploring], 'seed': seed,
        'prior_scale': model.scale, 'unlabeled_weight': model.unlabeled_weight,
        'weights': {column.name: weight for column, weight in zip(model_design.columns, weights[1:])},
        'findings': state_findings(model_design, weights),
    }}


def state_findings(model_design, weights):
    """What looks wrong in the fitted model that code can tell: a large weight on a present-or-absent feature that a
    single labeled listing carries, because one label is not evidence of a taste."""
    findings = []
    for column, weight in zip(model_design.columns, weights[1:]):
        if column.numeric or abs(weight) < SINGLE_LABEL_WEIGHT:
            continue
        carriers = [identifier for index, identifier in enumerate(model_design.ids)
                    if labeled(model_design.listings[identifier]) and model_design.raw[index].get(column.name) == 1.0]
        if len(carriers) == 1:
            findings.append(f'The weight of {column.name} ({weight:+.2f}) rests on a single labeled listing, '
                            f'{title(model_design.listings[carriers[0]])}.')
    return findings


def questions(inputs, hyperparameters, count, answered):
    """Up to count pairwise questions: pairs of ranked listings among the QUESTION_CANDIDATES best whose order the
    posterior is least sure of, that is whose probability of one ranking above the other is closest to one half,
    each listing in at most one question, without pairs already answered either way."""
    model = fit_model(inputs, hyperparameters)
    model_design, fit_result = model.design, model.fit
    order = ranked_order(model_design, fit_result.weights)[:QUESTION_CANDIDATES]
    values = scores(model_design.likelihood_rows, fit_result.weights, model_design.dense_count)
    spread = {index: fit_result.factor.forward([0.0, *model_design.rows[index]]) for index in order}
    answered = {frozenset(pair) for pair in answered}
    pairs = []
    for position, first in enumerate(order):
        for second in order[position + 1:]:
            if frozenset((model_design.ids[first], model_design.ids[second])) in answered:
                continue
            deviation = math.sqrt(sum((left - right) ** 2 for left, right in zip(spread[first], spread[second])))
            probability = 0.5 * (1 + math.erf((values[first] - values[second]) / (deviation * math.sqrt(2)))) if deviation else 1.0
            pairs.append((abs(probability - 0.5), model_design.ids[first], model_design.ids[second], probability))
    chosen, used = [], set()
    for certainty, first, second, probability in sorted(pairs):
        if len(chosen) == count:
            break
        if first in used or second in used:
            continue
        used |= {first, second}
        chosen.append({'listings': [first, second], 'probability_first_above': probability})
    return chosen

