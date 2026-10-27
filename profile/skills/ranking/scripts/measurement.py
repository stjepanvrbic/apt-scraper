"""Read-only store replay and seeded preference-learning experiments."""

import argparse
import copy
import json
import math
import random
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from statistics import NormalDist, fmean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gallery/scripts'))

from learning import decode, encode  # noqa: E402
from record import CATEGORIES, FLAGS, RATINGS  # noqa: E402
from scoring import design, dot, fit_model, leave_one_out, ranked_order, scores  # noqa: E402
from store import ListingStore  # noqa: E402


def bootstrap(values, statistic=median, *, seed=0, samples=2000, confidence=0.95):
    if not values:
        return None
    generator = random.Random(seed)
    estimates = sorted(statistic(generator.choices(values, k=len(values))) for _ in range(samples))
    tail = (1 - confidence) / 2
    return [estimates[int(tail * samples)], estimates[min(samples - 1, int((1 - tail) * samples))]]


def wilson(correct, total, confidence=0.95):
    if not total:
        return None
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    proportion = correct / total
    center = proportion + z * z / (2 * total)
    radius = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
    denominator = 1 + z * z / total
    return [(center - radius) / denominator, (center + radius) / denominator]


def utilities(model):
    return dict(zip(model.design.ids, scores(model.design.likelihood_rows, model.fit.weights, model.design.dense_count)))


def replay(events, fitter=fit_model):
    held_out, answers, excluded = [], [], 0
    for event in events:
        if event['kind'] not in ('favorite', 'contact', 'pair'):
            continue
        inputs = event['inputs']
        values = utilities(fitter(inputs))
        identifier = event['listing']
        if event['kind'] == 'pair':
            if identifier not in values or event['other'] not in values:
                excluded += 1
                continue
            difference = values[identifier] - values[event['other']]
            answers.append(difference > 0)
        else:
            candidates = [listing['id'] for listing in inputs['listings']
                          if listing['rankable'] and listing.get('rank') is not None and listing['id'] in values]
            if identifier not in candidates:
                excluded += 1
                continue
            order = sorted(candidates, key=lambda key: (-values[key], key))
            held_out.append({'listing': identifier, 'kind': event['kind'], 'rank': order.index(identifier) + 1, 'candidates': len(order)})
    ranks = [item['rank'] for item in held_out]
    top = [int(rank <= 20) for rank in ranks]
    return {'held_out': held_out, 'median_rank': median(ranks) if ranks else None, 'median_interval': bootstrap(ranks),
            'top_20': fmean(top) if top else None, 'top_20_interval': bootstrap(top, fmean),
            'pairwise_answers': len(answers), 'pairwise_accuracy': fmean(answers) if answers else None,
            'pairwise_interval': wilson(sum(answers), len(answers)),
            'pairwise_interval_90': wilson(sum(answers), len(answers), 0.90), 'excluded': excluded}


def garden(inputs, experiment, fitter=fit_model):
    favorites, alike = set(experiment['favorites']), set(experiment['alike'])
    available = {listing['id'] for listing in inputs['listings'] if listing['rankable']}
    if favorites & alike or not favorites or not alike or not (favorites | alike) <= available:
        raise ValueError('Garden favorites and unseen listings must be nonempty, disjoint and rankable.')
    result = {}
    for name, chosen in [('before', set()), ('after', favorites)]:
        snapshot = copy.deepcopy(inputs)
        for listing in snapshot['listings']:
            listing['favorite'] = listing['id'] in chosen
        model = fitter(snapshot)
        order = [model.design.ids[index] for index in ranked_order(model.design, model.fit.weights)]
        ranks = {identifier: position + 1 for position, identifier in enumerate(order)}
        prices = {listing['id']: listing['code'].get('monthly_price') for listing in snapshot['listings']}
        wins = comparisons = above = 0
        for identifier in alike:
            price = prices[identifier]
            peers = [other for other in order if other not in favorites | alike and price and prices[other]
                     and abs(prices[other] / price - 1) <= 0.1]
            won = sum(ranks[identifier] < ranks[other] for other in peers)
            wins += won
            comparisons += len(peers)
            above += bool(peers) and won == len(peers)
        unseen = [ranks[identifier] for identifier in sorted(alike)]
        result[name] = {'wins': wins, 'comparisons': comparisons, 'above_all': above, 'mean_rank': fmean(unseen),
                        'mean_interval': bootstrap(unseen, fmean), 'leave_one_out': leave_one_out(model).ranks}
    return result


def read_copy(source, callback):
    source = Path(source).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='ranking-measure-') as directory:
        target = Path(directory) / 'listings.sqlite3'
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original, closing(sqlite3.connect(target)) as copied:
            original.backup(copied)
        return callback(ListingStore(target))


def store_report(source, experiment=None, fitter=fit_model):
    def measure(store):
        inputs = store.ranking_inputs()
        with store.connect() as connection:
            events = [{**dict(row), 'inputs': decode(row['inputs'])} for row in connection.execute('SELECT * FROM learning_events ORDER BY id')]
        result = {'listings': len(inputs['listings']), 'events': len(events), 'prequential': replay(events, fitter),
                  'leave_one_out': leave_one_out(fitter(inputs)).ranks,
                  'history_limit': 'Only journaled actions have their original records, settings and candidate sets. Older labels cannot be replayed.'}
        if experiment:
            result['garden'] = garden(inputs, experiment, fitter)
        return result
    return read_copy(source, measure)


def agreement(first, second):
    if len(first) != len(second) or not first:
        raise ValueError('Two equally sized nonempty extraction samples are required.')
    fields = {}
    for left, right in zip(first, second):
        left_rooms = {photo['index']: photo['room'] for photo in left['photos']}
        right_rooms = {photo['index']: photo['room'] for photo in right['photos']}
        if left_rooms.keys() != right_rooms.keys():
            raise ValueError('Repeated extractions must cover the same photos.')
        fields.setdefault('photo_room', []).extend(left_rooms[index] == right_rooms[index] for index in left_rooms)
        for field in CATEGORIES:
            fields.setdefault(field, []).append(left[field] == right[field])
        fields.setdefault('unit_level', []).append(left['unit_level']['value'] == right['unit_level']['value'])
        for flag in FLAGS:
            fields.setdefault(f'flag:{flag}', []).append(left['flags'][flag] == right['flags'][flag])
        for rating in RATINGS:
            a, b = left['ratings'][rating], right['ratings'][rating]
            equal = a == b if isinstance(a, str) or isinstance(b, str) else abs(a['value'] - b['value']) <= 1
            fields.setdefault(f'rating:{rating}', []).append(equal)
        for name in ('traits', 'concerns'):
            a, b = set(left[name]), set(right[name])
            fields.setdefault(name, []).append(len(a & b) / len(a | b) if a | b else 1.0)
    means = {name: fmean(values) for name, values in fields.items() if values}
    return {'listings': len(first), 'fields': means, 'below_0.7': [name for name, value in means.items() if value < 0.7]}


def simulate(inputs, selector, fitter=fit_model, *, runs=200, labels=40, seed=0):
    """Independent sparse tastes; noisy answers to displayed pairs; fixed unseen evaluation pairs per run."""
    blank = copy.deepcopy(inputs)
    blank.update(pairs=[], note_readings={}, sentiments={})
    for listing in blank['listings']:
        listing.update(favorite=False, dismissed=False, contact=False, user_notes=False, paired=False)
    base = design(blank)
    if len(base.ids) < 4 or not base.columns:
        raise ValueError('Simulation needs at least four listings with varying features.')
    curves = {count: [] for count in range(0, labels + 1, 5)}
    run_curves, trajectories = [], []
    for run in range(runs):
        generator = random.Random(seed + run)
        selected = generator.sample(range(len(base.columns)), min(5, len(base.columns)))
        weights = [generator.gauss(0, 2) if index in selected else 0 for index in range(len(base.columns))]
        taste = dict(zip(base.ids, (dot(row, weights) for row in base.rows)))
        held_pairs = [generator.sample(base.ids, 2) for _ in range(100)]
        state = copy.deepcopy(blank)
        curve = []
        for count in range(labels + 1):
            if count in curves:
                values = utilities(fitter(state))
                accuracy = fmean(0.5 if values[a] == values[b] else (values[a] > values[b]) == (taste[a] > taste[b]) for a, b in held_pairs)
                curves[count].append(accuracy)
                curve.append(accuracy)
            if count == labels:
                break
            asked = selector(state, 1, [*state['pairs'], *held_pairs])
            if not asked:
                raise ValueError('The selector exhausted pairs before the requested label count.')
            first, second = asked[0]['listings']
            chosen, other = (first, second) if taste[first] - taste[second] + generator.gauss(0, 0.3) > 0 else (second, first)
            state['pairs'].append((chosen, other))
            for listing in state['listings']:
                listing['paired'] = listing['paired'] or listing['id'] in (chosen, other)
        run_curves.append(curve)
        trajectories.append({'pairs': state['pairs'], 'held_pairs': held_pairs, 'taste': taste})
    return {'runs': runs, 'seed': seed, 'labels': labels, 'curves': [
        {'labels': count, 'accuracy': fmean(values), 'interval': bootstrap(values, fmean)} for count, values in curves.items()],
        'run_curves': run_curves, 'trajectories': trajectories, 'inputs': encode(blank)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('store', type=Path)
    parser.add_argument('--experiment', type=Path)
    parser.add_argument('--simulate', type=int, default=0, metavar='RUNS')
    parser.add_argument('--labels', type=int, default=40)
    parser.add_argument('--agreement', nargs=2, type=Path)
    args = parser.parse_args()
    report = store_report(args.store, json.loads(args.experiment.read_text()) if args.experiment else None)
    if args.simulate:
        from scoring import DEFAULT_HYPERPARAMETERS, questions
        report['simulation'] = read_copy(args.store, lambda store: simulate(
            store.ranking_inputs(), lambda inputs, count, answered: questions(inputs, DEFAULT_HYPERPARAMETERS, count, answered),
            runs=args.simulate, labels=args.labels))
    if args.agreement:
        report['agreement'] = agreement(*(json.loads(path.read_text()) for path in args.agreement))
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
