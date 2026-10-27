"""Experimental grouped priors, evaluated before changing the installed scoring model."""

import copy
import math
from dataclasses import replace
from statistics import fmean, pstdev

from scoring import COMPOSITES, Column, Model, design, display, fit, label, objective, observations, prior, sparse_row
SCALE_FLOOR = 0.1
AGE_FLOOR = 0.1
HALF_LIVES = (math.inf, 90, 30, 7)


def composite_design(inputs):
    original = design(inputs)
    dense = original.dense_count
    columns = list(original.columns[:dense])
    values = [list(row[:dense]) for row in original.rows]
    raw = [dict(row) for row in original.raw]
    means = list(original.prior_mean[:dense])
    for name, components in COMPOSITES.items():
        positions = [original.index(f'rating:{component}') for component in components]
        positions = [position for position in positions if position is not None]
        if not positions:
            continue
        averages = [fmean(row[position] for position in positions) for row in original.rows]
        spread = 2 * pstdev(averages)
        if not spread:
            continue
        prior_mean = sum(original.prior_mean[position] for position in positions) * spread
        for row, average, stated in zip(values, averages, raw):
            for position in positions:
                row[position] -= average
            row.append(average / spread)
            known = [stated[f'rating:{component}'] for component in components if f'rating:{component}' in stated]
            if known:
                stated[f'composite:{name}'] = fmean(known)
        columns.append(Column(f'composite:{name}', True, False, 0, spread))
        means.append(prior_mean)
    new_dense = len(columns)
    columns += list(original.columns[dense:])
    rows = tuple(row + list(old[dense:]) for row, old in zip(values, original.rows))
    likelihood = tuple(sparse_row([1.0, *row[:new_dense]], {
        position: value + column.mean for position, (column, value) in enumerate(zip(columns[new_dense:], row[new_dense:]))
    }) for row in rows)
    return replace(original, columns=tuple(columns), dense_count=new_dense, rows=rows, likelihood_rows=likelihood,
                   raw=tuple(raw), prior_mean=tuple(means + list(original.prior_mean[dense:])))


def group(name):
    if name.startswith(('tag:', 'hypothesis:')):
        return name
    if name.startswith('composite:'):
        return name
    if name.startswith(('area:', 'place:')) or name in ('near_favorites', 'station_distance'):
        return 'location'
    if name.startswith('preference:'):
        return 'judgments'
    if name.startswith('flag:'):
        return 'flags'
    if name.startswith('rating:'):
        rating = name.split(':', 1)[1]
        if rating.startswith('kitchen'):
            return 'kitchen'
        if rating.startswith('bathroom'):
            return 'bathroom'
        return 'size and layout' if rating in COMPOSITES['space'] else 'quality'
    if ':' in name:
        return 'categories'
    return 'price and size'


def prior_variances(model_design):
    components = {f'rating:{rating}' for ratings in COMPOSITES.values() for rating in ratings}
    return [1e6, *[0.25 ** 2 if column.name in components else 1.0 for column in model_design.columns]]


def aged(data, inputs, half_life):
    now = inputs.get('as_of_day', 0)
    listing_days = inputs.get('label_days', {})
    explicit = {listing['id'] for listing in inputs['listings'] if label(listing, inputs['sentiments'].get(listing['id'])) is not None}
    pair_days = dict(zip(map(tuple, inputs['pairs']), inputs.get('pair_days', [])))
    weights = []
    for weight, listing, pair in zip(data.weights, data.listings, data.pairs):
        day = pair_days.get(pair) if pair else listing_days.get(listing) if listing in explicit else None
        decay = max(AGE_FLOOR, 2 ** (-max(0, now - day) / half_life)) if day is not None else 1.0
        weights.append(weight * decay)
    return replace(data, weights=weights)


def evidence(data, result, mean, precision, dense_count):
    return (-objective(data, result.weights, mean, precision, dense_count)
            + sum(math.log(value) for value in precision) / 2
            - sum(math.log(row[-1]) for row in result.factor.lower))


def fit_candidate(inputs, *, half_life=math.inf, evidence_steps=20):
    model_design = composite_design(inputs)
    mean, _ = prior(model_design, 1)
    data = aged(observations(model_design, inputs, 0.05), inputs, half_life)
    variances = prior_variances(model_design)
    precision = [1 / variance for variance in variances]
    full = fit(data, mean, precision, model_design.dense_count)
    count = len(inputs['pairs']) + sum(label(listing, inputs['sentiments'].get(listing['id'])) is not None for listing in inputs['listings'])
    if count < 10:
        return Model(model_design, full, 1.0, 0.05, data, (mean, precision))
    groups = {}
    for position, column in enumerate(model_design.columns, 1):
        groups.setdefault(group(column.name), []).append(position)
    base_variances = prior_variances(model_design)
    current = evidence(data, full, mean, precision, model_design.dense_count)
    for _ in range(evidence_steps):
        diagonal = []
        for position in range(len(mean)):
            unit = [float(index == position) for index in range(len(mean))]
            transformed = full.factor.forward(unit)
            diagonal.append(sum(value * value for value in transformed))
        proposed = list(variances)
        for positions in groups.values():
            scale = fmean(((full.weights[position] - mean[position]) ** 2 + diagonal[position]) / base_variances[position]
                         for position in positions)
            for position in positions:
                proposed[position] = max(SCALE_FLOOR ** 2, min(4.0, scale * base_variances[position]))
        accepted = False
        for fraction in (1.0, 0.5, 0.25):
            candidate_variances = [math.exp((1 - fraction) * math.log(old) + fraction * math.log(new))
                                   for old, new in zip(variances, proposed)]
            candidate_precision = [1 / variance for variance in candidate_variances]
            candidate = fit(data, mean, candidate_precision, model_design.dense_count, full.weights)
            value = evidence(data, candidate, mean, candidate_precision, model_design.dense_count)
            if value > current + 1e-6:
                variances, precision, full, current = candidate_variances, candidate_precision, candidate, value
                accepted = True
                break
        if not accepted:
            break
    return Model(model_design, full, 1.0, 0.05, data, (mean, precision))


def choose_half_life(history):
    """Compare settings only on already-observed outcomes and their original pre-action snapshots."""
    from measurement import utilities
    if len(history) < 10:
        return math.inf
    losses = {}
    for half_life in HALF_LIVES:
        losses[half_life] = 0
        for event in history[-10:]:
            values = utilities(fit_candidate(event['inputs'], half_life=half_life, evidence_steps=0))
            difference = values[event['listing']] - values[event['other']]
            losses[half_life] += max(-difference, 0) + math.log1p(math.exp(-abs(difference)))
    return min(HALF_LIVES, key=losses.get)


def pair_history(inputs):
    history = []
    for index, (chosen, other) in enumerate(inputs['pairs']):
        snapshot = copy.deepcopy(inputs)
        snapshot['pairs'] = inputs['pairs'][:index]
        snapshot['pair_days'] = inputs.get('pair_days', [])[:index]
        snapshot['as_of_day'] = inputs.get('pair_days', [0] * len(inputs['pairs']))[index]
        history.append({'kind': 'pair', 'listing': chosen, 'other': other, 'inputs': snapshot})
    return history


def tuned_candidate(inputs):
    return fit_candidate(inputs, half_life=choose_half_life(pair_history(inputs)))


def composite_reason(model, identifier, name):
    return display(model.design, model.design.ids.index(identifier), f'composite:{name}')
