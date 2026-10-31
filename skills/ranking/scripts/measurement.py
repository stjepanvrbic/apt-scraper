"""Read-only store replay and seeded preference-learning experiments."""

import argparse
import copy
import json
import math
import random
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from datetime import datetime
from math import sumprod as dot
from pathlib import Path
from statistics import NormalDist, fmean, median
from typing import NotRequired, TypedDict, cast, overload

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gallery/scripts'))

from learning import LearningEvent, decode, encode, prediction  # noqa: E402
from record import FLAGS, RATINGS, Extraction  # noqa: E402
from scoring import CATEGORY_NAMES, Model, Pair, Question, RankingInputs, design, fit_model, labeled, leave_one_out, ranked_order, scores, text_array  # noqa: E402
from shapes import is_object, loads  # noqa: E402
from store import ListingStore, select  # noqa: E402

PAIR_TIE_POLICY = 'Chance credit for tied model scores or tied latent tastes; bootstrap over runs.'

Interval = list[float]
"""The two ends of a confidence interval."""


class ReplayEvent(LearningEvent):
    """A user action with the ranking inputs as they were before it, which the journal keeps and the simulation
    builds."""
    inputs: RankingInputs


class JournalEvent(ReplayEvent):
    """A learning_events row with its inputs decoded."""
    id: int
    occurred_at: str


class HistoryInputs(RankingInputs):
    """The ranking inputs with the actions journaled before them, which a history-aware fitter reads."""
    history: Sequence[ReplayEvent]


Fitter = Callable[[RankingInputs], Model]
HistoryFitter = Callable[[HistoryInputs], Model]
Selector = Callable[[HistoryInputs, int, Sequence[Sequence[str]]], Sequence[Question]]
"""A pairwise question selector: the inputs with the history, the number of questions and the pairs already asked."""


class SkipPair(TypedDict):
    """A favorite over a listing shown above it in the same visit and never labeled, at a diagnostic weight."""
    chosen: str
    other: str
    weight: float


class HeldOutAction(TypedDict):
    """A favorite or contact request scored by the model refitted on the journal before it."""
    listing: str
    kind: str
    rank: int
    displayed_rank: int | None
    candidates: int
    already_positive: bool


class ReplayReport(TypedDict):
    held_out: list[HeldOutAction]
    known_positive_actions: list[HeldOutAction]
    rank_definition: str
    displayed_median_rank: float | None
    displayed_median_interval: Interval | None
    median_rank: float | None
    median_interval: Interval | None
    top_20: float | None
    top_20_interval: Interval | None
    pairwise_answers: int
    pairwise_scored: int
    pairwise_ties: int
    pairwise_unscorable: int
    pairwise_accuracy: float | None
    pairwise_interval: Interval | None
    pairwise_interval_90: Interval | None
    excluded: int
    skip_above_diagnostic: list[SkipPair]


class GardenExperiment(TypedDict):
    """The favorites to label and the unseen listings like them whose ranks the garden check follows."""
    favorites: list[str]
    alike: list[str]


class GardenResult(TypedDict):
    wins: int
    comparisons: int
    above_all: int
    mean_rank: float
    mean_interval: Interval | None
    leave_one_out: dict[str, int]


AgreementReport = TypedDict('AgreementReport', {
    'listings': int, 'fields': dict[str, float], 'intervals_95': dict[str, Interval | None], 'interval_method': str,
    'below_0.7': list[str]})


class CurvePoint(TypedDict):
    labels: int
    accuracy: float
    interval: Interval | None


class Trajectory(TypedDict):
    """One simulated run: the pairs answered in order, the held-out pairs scored and the latent taste by listing."""
    pairs: list[Pair]
    held_pairs: list[list[str]]
    taste: dict[str, float]


class SimulationReport(TypedDict):
    runs: int
    seed: int
    labels: int
    curves: list[CurvePoint]
    run_curves: list[list[float]]
    trajectories: list[Trajectory]
    inputs: str
    tie_policy: str


class StoreReport(TypedDict):
    listings: int
    events: int
    prequential: ReplayReport
    leave_one_out: dict[str, int]
    history_limit: str
    garden: NotRequired[dict[str, GardenResult]]
    simulation: NotRequired[SimulationReport]
    agreement: NotRequired[AgreementReport]


@overload
def bootstrap(values: Sequence[float], statistic: Callable[[list[float]], float] = ..., *, seed: int = ..., samples: int = ...,
              confidence: float = ...) -> Interval | None: ...
@overload
def bootstrap[T](values: Sequence[T], statistic: Callable[[list[T]], float], *, seed: int = ..., samples: int = ...,
                 confidence: float = ...) -> Interval | None: ...
def bootstrap[T](values: Sequence[T], statistic: Callable[[list[T]], float] = median,  # pyright: ignore[reportArgumentType]  # the default serves the first overload, whose values are numbers
                 *, seed: int = 0, samples: int = 2000, confidence: float = 0.95) -> Interval | None:
    if not values:
        return None
    generator = random.Random(seed)
    estimates = sorted(statistic(generator.choices(values, k=len(values))) for _ in range(samples))
    tail = (1 - confidence) / 2
    lower = int(tail * samples)
    return [estimates[lower], estimates[samples - 1 - lower]]


def wilson(correct: float, total: int, confidence: float = 0.95) -> Interval | None:
    if not total:
        return None
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    proportion = correct / total
    center = proportion + z * z / (2 * total)
    radius = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
    denominator = 1 + z * z / total
    return [(center - radius) / denominator, (center + radius) / denominator]


def utilities(model: Model) -> dict[str, float]:
    return dict(zip(model.design.ids, scores(model.design.likelihood_rows, model.fit.weights, model.design.dense_count)))


def skip_above(events: Sequence[ReplayEvent]) -> list[SkipPair]:
    pairs: list[SkipPair] = []
    for event in events:
        impressions = event['inputs'].get('impressions')
        if event['kind'] != 'favorite' or not impressions:
            continue
        journaled = cast(JournalEvent, event)  # impressions are recorded with a journaled favorite, which has its time
        timestamp = datetime.fromisoformat(journaled['occurred_at'])
        listings = {listing['id']: listing for listing in event['inputs']['listings']}
        seen = [item for item in impressions if datetime.fromisoformat(item['occurred_at']) <= timestamp
                and item['listing_id'] in listings and listings[item['listing_id']]['fingerprint'] == item['fingerprint']]
        own = [item for item in seen if item['listing_id'] == event['listing']]
        if not own:
            continue
        selected = max(own, key=lambda item: datetime.fromisoformat(item['occurred_at']))
        skipped = {item['listing_id'] for item in seen if item['visit_id'] == selected['visit_id']
                   and item['shown_rank'] < selected['shown_rank'] and item['listing_id'] != event['listing']
                   and not labeled(listings[item['listing_id']])}
        pairs.extend({'chosen': event['listing'], 'other': identifier, 'weight': 0.1} for identifier in sorted(skipped))
    return pairs


def replay(events: Sequence[ReplayEvent], fitter: HistoryFitter = fit_model) -> ReplayReport:
    held_out: list[HeldOutAction] = []
    known_positive: list[HeldOutAction] = []
    answers: list[float] = []
    excluded, ties = 0, 0
    for index, event in enumerate(events):
        if event['kind'] not in ('favorite', 'contact', 'pair'):
            continue
        inputs: HistoryInputs = {**event['inputs'], 'history': events[:index]}
        result = prediction(inputs, event, fitter)  # pyright: ignore[reportArgumentType]  # prediction fits these inputs, which carry the history
        if result is None:
            excluded += 1
        elif result['kind'] == 'pair':
            if result['tied']:
                ties += 1
            else:
                answers.append(result['value'])
        else:
            destination = known_positive if result['already_positive'] else held_out
            destination.append({'listing': event['listing'], 'kind': event['kind'], 'rank': result['value'],
                                'displayed_rank': result['displayed_rank'], 'candidates': result['candidates'],
                                'already_positive': result['already_positive']})
    ranks = [item['rank'] for item in held_out]
    displayed = [item['displayed_rank'] for item in held_out if item['displayed_rank'] is not None]
    top = [int(rank <= 20) for rank in ranks]
    pair_count = sum(event['kind'] == 'pair' for event in events)
    return {'held_out': held_out, 'known_positive_actions': known_positive,
            'rank_definition': 'Refitted model rank over the historical candidate set, for model comparisons.',
            'displayed_median_rank': median(displayed) if displayed else None, 'displayed_median_interval': bootstrap(displayed),
            'median_rank': median(ranks) if ranks else None, 'median_interval': bootstrap(ranks),
            'top_20': fmean(top) if top else None, 'top_20_interval': bootstrap(top, fmean),
            'pairwise_answers': pair_count, 'pairwise_scored': len(answers), 'pairwise_ties': ties,
            'pairwise_unscorable': pair_count - len(answers) - ties, 'pairwise_accuracy': fmean(answers) if answers else None,
            'pairwise_interval': wilson(sum(answers), len(answers)),
            'pairwise_interval_90': wilson(sum(answers), len(answers), 0.90), 'excluded': excluded,
            'skip_above_diagnostic': skip_above(events)}


def garden_experiment(value: object) -> GardenExperiment:
    """The garden experiment as its file states it: the favorites and the unseen listings like them, by id."""
    if not is_object(value) or set(value) != {'favorites', 'alike'}:
        raise ValueError('A garden experiment file holds favorites and alike, each a list of listing ids.')
    favorites, alike = text_array(value['favorites']), text_array(value['alike'])
    if favorites is None or alike is None:
        raise ValueError('A garden experiment file holds favorites and alike, each a list of listing ids.')
    return {'favorites': favorites, 'alike': alike}


def garden(inputs: RankingInputs, experiment: GardenExperiment, fitter: Fitter = fit_model) -> dict[str, GardenResult]:
    favorites, alike = set(experiment['favorites']), set(experiment['alike'])
    available = {listing['id'] for listing in inputs['listings'] if listing['rankable']}
    if favorites & alike or not favorites or not alike or not (favorites | alike) <= available:
        raise ValueError('Garden favorites and unseen listings must be nonempty, disjoint and rankable.')
    result: dict[str, GardenResult] = {}
    for name, chosen in [('before', set[str]()), ('after', favorites)]:
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
            peers = [other for other in order if other not in favorites | alike and price and (peer_price := prices[other])
                     and abs(peer_price / price - 1) <= 0.1]
            won = sum(ranks[identifier] < ranks[other] for other in peers)
            wins += won
            comparisons += len(peers)
            above += bool(peers) and won == len(peers)
        unseen = [ranks[identifier] for identifier in sorted(alike)]
        result[name] = {'wins': wins, 'comparisons': comparisons, 'above_all': above, 'mean_rank': fmean(unseen),
                        'mean_interval': bootstrap(unseen, fmean), 'leave_one_out': leave_one_out(model).ranks}
    return result


def read_copy[T](source: str | Path, callback: Callable[[ListingStore], T]) -> T:
    source = Path(source).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='ranking-measure-') as directory:
        target = Path(directory) / 'listings.sqlite3'
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original, closing(sqlite3.connect(target)) as copied:
            original.backup(copied)
        return callback(ListingStore(target))


def journal_event(row: sqlite3.Row) -> JournalEvent:
    """A learning_events row with its inputs decoded."""
    return {'id': cast(int, row['id']), 'occurred_at': cast(str, row['occurred_at']), 'kind': cast(str, row['kind']),
            'listing': cast(str, row['listing']), 'other': cast(str | None, row['other']), 'inputs': decode(cast(str, row['inputs']))}


def store_report(source: str | Path, experiment: GardenExperiment | None = None, fitter: Fitter = fit_model) -> StoreReport:
    def measure(store: ListingStore) -> StoreReport:
        inputs = store.ranking_inputs()
        with store.connect() as connection:
            events = [journal_event(row) for row in select(connection, 'SELECT * FROM learning_events ORDER BY id')]
        history: HistoryInputs = {**inputs, 'history': events}
        result: StoreReport = {'listings': len(inputs['listings']), 'events': len(events), 'prequential': replay(events, fitter),
                               'leave_one_out': leave_one_out(fitter(history)).ranks,
                               'history_limit': 'Only journaled actions have their original records, settings and candidate sets. Older labels cannot be replayed.'}
        if experiment:
            result['garden'] = garden(inputs, experiment, fitter)
        return result
    return read_copy(source, measure)


def agreement(first: Sequence[Extraction], second: Sequence[Extraction]) -> AgreementReport:
    if len(first) != len(second) or not first:
        raise ValueError('Two equally sized nonempty extraction samples are required.')
    fields: dict[str, list[float]] = {}
    rooms: list[tuple[int, int]] = []
    for left, right in zip(first, second):
        left_rooms = {photo['index']: photo['room'] for photo in left['photos']}
        right_rooms = {photo['index']: photo['room'] for photo in right['photos']}
        if left_rooms.keys() != right_rooms.keys():
            raise ValueError('Repeated extractions must cover the same photos.')
        fields.setdefault('photo_room', []).extend(left_rooms[index] == right_rooms[index] for index in left_rooms)
        rooms.append((sum(left_rooms[index] == right_rooms[index] for index in left_rooms), len(left_rooms)))
        for field in CATEGORY_NAMES:
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
    intervals = {name: bootstrap(values, fmean) if name in ('traits', 'concerns') else wilson(sum(values), len(values))
                 for name, values in fields.items() if values}
    observed_rooms = [counts for counts in rooms if counts[1]]
    if observed_rooms:
        intervals['photo_room'] = bootstrap(observed_rooms, lambda counts: sum(hit for hit, _ in counts) / sum(total for _, total in counts))
    return {'listings': len(first), 'fields': means, 'intervals_95': intervals,
            'interval_method': 'Wilson for binary matches; bootstrap over listings for tags and clustered room matches',
            'below_0.7': [name for name, value in means.items() if value < 0.7]}


def simulation_start(inputs: RankingInputs) -> RankingInputs:
    blank = copy.deepcopy(inputs)
    blank.update(pairs=[], note_readings={}, sentiments={}, label_days={}, pair_days=[], as_of_day=0)
    _ = blank.pop('history', None)
    for listing in blank['listings']:
        listing.update(favorite=False, dismissed=False, contact=False, user_notes=False, paired=False)
    return blank


def record_simulated_pair(state: RankingInputs, chosen: str, other: str, history: list[ReplayEvent]) -> None:
    """Records are fixed during simulation; copy only the state an answer can change."""
    pair_days = state['pair_days']  # pyright: ignore[reportTypedDictNotRequiredAccess]  # simulation_start sets the clock
    before: RankingInputs = {**state, 'pairs': state['pairs'][:], 'pair_days': pair_days[:],
                             'listings': [{**listing} for listing in state['listings']]}
    history.append({'kind': 'pair', 'listing': chosen, 'other': other, 'inputs': before})
    pair_days.append(len(state['pairs']))
    state['pairs'].append((chosen, other))
    state['as_of_day'] = len(state['pairs'])
    for listing in state['listings']:
        listing['paired'] = listing['paired'] or listing['id'] in (chosen, other)


def simulate(inputs: RankingInputs, selector: Selector, fitter: HistoryFitter = fit_model, *, runs: int = 200, labels: int = 40,
             seed: int = 0) -> SimulationReport:
    """Independent sparse tastes; noisy answers to displayed pairs; fixed unseen evaluation pairs per run."""
    blank = simulation_start(inputs)
    base = design(blank)
    if len(base.ids) < 4 or not base.columns:
        raise ValueError('Simulation needs at least four listings with varying features.')
    curves: dict[int, list[float]] = {count: [] for count in range(0, labels + 1, 5)}
    run_curves: list[list[float]] = []
    trajectories: list[Trajectory] = []
    for run in range(runs):
        generator = random.Random(seed + run)
        selected = generator.sample(range(len(base.columns)), min(5, len(base.columns)))
        weights: list[float] = [generator.gauss(0, 2) if index in selected else 0 for index in range(len(base.columns))]
        taste = dict(zip(base.ids, (dot(row, weights) for row in base.rows)))
        held_pairs = [generator.sample(base.ids, 2) for _ in range(100)]
        state = copy.deepcopy(blank)
        curve: list[float] = []
        history: list[ReplayEvent] = []
        for count in range(labels + 1):
            context: HistoryInputs = {**state, 'history': history}
            if count in curves:
                values = utilities(fitter(context))
                accuracy = pair_accuracy(values, taste, held_pairs)
                curves[count].append(accuracy)
                curve.append(accuracy)
            if count == labels:
                break
            asked = selector(context, 1, [*state['pairs'], *held_pairs])
            if not asked:
                raise ValueError('The selector exhausted pairs before the requested label count.')
            first, second = asked[0]['listings']
            chosen, other = (first, second) if taste[first] - taste[second] + generator.gauss(0, 0.3) > 0 else (second, first)
            record_simulated_pair(state, chosen, other, history)
        run_curves.append(curve)
        trajectories.append({'pairs': state['pairs'], 'held_pairs': held_pairs, 'taste': taste})
    return {'runs': runs, 'seed': seed, 'labels': labels, 'curves': [
        {'labels': count, 'accuracy': fmean(values), 'interval': bootstrap(values, fmean)} for count, values in curves.items()],
        'run_curves': run_curves, 'trajectories': trajectories, 'inputs': encode(blank), 'tie_policy': PAIR_TIE_POLICY}


def pair_accuracy(values: Mapping[str, float], taste: Mapping[str, float], pairs: Iterable[Sequence[str]]) -> float:
    return fmean(0.5 if values[a] == values[b] or taste[a] == taste[b] else (values[a] > values[b]) == (taste[a] > taste[b])
                 for a, b in pairs)


class Arguments(argparse.Namespace):
    store: Path = Path()
    experiment: Path | None = None
    simulate: int = 0
    labels: int = 40
    agreement: list[Path] | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('store', type=Path)
    _ = parser.add_argument('--experiment', type=Path)
    _ = parser.add_argument('--simulate', type=int, default=0, metavar='RUNS')
    _ = parser.add_argument('--labels', type=int, default=40)
    _ = parser.add_argument('--agreement', nargs=2, type=Path)
    args = parser.parse_args(namespace=Arguments())
    report = store_report(args.store, garden_experiment(loads(args.experiment.read_text())) if args.experiment else None)
    if args.simulate:
        from scoring import DEFAULT_HYPERPARAMETERS, questions
        report['simulation'] = read_copy(args.store, lambda store: simulate(
            store.ranking_inputs(), lambda inputs, count, answered: questions(inputs, DEFAULT_HYPERPARAMETERS, count, answered),
            runs=args.simulate, labels=args.labels))
    if args.agreement:
        report['agreement'] = agreement(*(cast(list[Extraction], loads(path.read_text())) for path in args.agreement))
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
