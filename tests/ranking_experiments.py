"""Reproduce ranking measurements with synthetic listing stores and fixed random seeds."""

import argparse
import gzip
import hashlib
import json
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import fmean

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/ranking/scripts'))

from test_rerank import SETTINGS, fill, least_cpu_seconds  # noqa: E402
from test_scoring import GARDEN, garden_neighborhood, inputs  # noqa: E402
from measurement import PAIR_TIE_POLICY, bootstrap, garden, simulate, store_report  # noqa: E402
from rerank import rerank  # noqa: E402
from scoring import DEFAULT_HYPERPARAMETERS, questions  # noqa: E402
from store import ListingStore  # noqa: E402


def simulation_batch(arguments):
    sample, runs, labels, seed = arguments
    return simulate(sample, lambda state, count, answered: questions(state, DEFAULT_HYPERPARAMETERS, count, answered),
                    runs=runs, labels=labels, seed=seed)


def parallel_simulation(sample, runs, labels, workers):
    batches = [(sample, min(10, runs - seed), labels, seed) for seed in range(0, runs, 10)]
    curves, trajectories = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(simulation_batch, batches):
            curves.extend(result['run_curves'])
            trajectories.extend(result['trajectories'])
            print(f'{len(curves)}/{runs} simulated users complete', flush=True)
    return {'runs': runs, 'seed': 0, 'labels': labels, 'curves': [
        {'labels': index * 5, 'accuracy': fmean(values), 'interval': bootstrap(list(values), fmean)}
        for index, values in enumerate(zip(*curves))], 'run_curves': curves, 'trajectories': trajectories, 'inputs': result['inputs'],
        'tie_policy': PAIR_TIE_POLICY}


def write_report(report, output):
    simulation = report['simulation']
    feedback = {name: simulation.pop(name) for name in ('inputs', 'trajectories')}
    compressed = gzip.compress(json.dumps(feedback, separators=(',', ':')).encode(), mtime=0)
    artifact = output.with_suffix('.feedback.json.gz')
    artifact.write_bytes(compressed)
    simulation.update(feedback_file=artifact.name, feedback_sha256=hashlib.sha256(compressed).hexdigest())
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def chronological_fixture(directory):
    store = ListingStore(directory / 'chronological.sqlite3')
    store.update_settings(**SETTINGS)
    identifiers = fill(store, 24, 0)
    for index in (3, 8, 14, 21):
        rerank(store, seed=1)
        store.feedback(identifiers[index], favorite=True)
    rerank(store, seed=1)
    store.request_contact(identifiers[5])
    store.answer_pair(identifiers[6], identifiers[7])
    return store_report(store.path)['prequential']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=int, default=200)
    parser.add_argument('--labels', type=int, default=40)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='ranking-experiment-') as directory:
        store = ListingStore(Path(directory) / 'listings.sqlite3')
        store.update_settings(**SETTINGS)
        fill(store, 300, 50)
        seconds, _ = least_cpu_seconds(lambda: rerank(store, seed=1))
        report = {'data': 'synthetic; no real store available', 'rerank_cpu_seconds': seconds}
        report['prequential_fixture'] = chronological_fixture(Path(directory))
        report['store'] = store_report(store.path)
        problem = inputs(garden_neighborhood())
        identifiers = [listing['id'] for listing in problem['listings']]
        report['garden'] = garden(problem, {'favorites': [identifiers[index] for index in GARDEN[:5]], 'alike': [identifiers[GARDEN[5]]]})
        sample = store.ranking_inputs()
        sample['listings'] = sample['listings'][:80]
        report['simulation'] = parallel_simulation(sample, args.runs, args.labels, args.workers)
        write_report(report, args.output)


if __name__ == '__main__':
    main()
