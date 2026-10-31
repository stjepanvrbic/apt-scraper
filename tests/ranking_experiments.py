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
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/ranking/scripts'))

from test_rerank import SETTINGS, fill, least_cpu_seconds  # noqa: E402
from test_scoring import GARDEN, garden_neighborhood, inputs  # noqa: E402
from measurement import (PAIR_TIE_POLICY, CurvePoint, GardenResult, ReplayReport, SimulationReport, StoreReport, Trajectory, bootstrap,  # noqa: E402
                         garden, simulate, store_report)
from rerank import rerank  # noqa: E402
from scoring import DEFAULT_HYPERPARAMETERS, RankingInputs, questions  # noqa: E402
from store import ListingStore  # noqa: E402


class ReportWithSimulation(TypedDict):
    """A report whose simulation write_report freezes into a companion file."""
    simulation: SimulationReport


class ExperimentReport(ReportWithSimulation):
    """The report of a run over synthetic stores."""
    data: str
    rerank_cpu_seconds: float
    prequential_fixture: ReplayReport
    store: StoreReport
    garden: dict[str, GardenResult]


class FrozenFeedback(TypedDict):
    """What the companion file holds: the simulation's starting inputs and the trajectory of every run."""
    inputs: str
    trajectories: list[Trajectory]


class SavedSimulation(TypedDict):
    """A written report's simulation: its frozen feedback moved to the companion file, named with its digest."""
    runs: int
    seed: int
    labels: int
    curves: list[CurvePoint]
    run_curves: list[list[float]]
    tie_policy: str
    feedback_file: str
    feedback_sha256: str


class SavedReport(TypedDict):
    """A written report as the tools that build on it open it; its other keys vary by experiment."""
    simulation: SavedSimulation


def simulation_batch(arguments: tuple[RankingInputs, int, int, int]) -> SimulationReport:
    sample, runs, labels, seed = arguments
    return simulate(sample, lambda state, count, answered: questions(state, DEFAULT_HYPERPARAMETERS, count, answered),
                    runs=runs, labels=labels, seed=seed)


def parallel_simulation(sample: RankingInputs, runs: int, labels: int, workers: int) -> SimulationReport:
    batches = [(sample, min(10, runs - seed), labels, seed) for seed in range(0, runs, 10)]
    curves: list[list[float]] = []
    trajectories: list[Trajectory] = []
    encoded: str | None = None
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(simulation_batch, batches):
            curves.extend(result['run_curves'])
            trajectories.extend(result['trajectories'])
            encoded = result['inputs']
            print(f'{len(curves)}/{runs} simulated users complete', flush=True)
    if encoded is None:
        raise ValueError('Simulation needs at least one run.')
    return {'runs': runs, 'seed': 0, 'labels': labels, 'curves': [
        {'labels': index * 5, 'accuracy': fmean(values), 'interval': bootstrap(list(values), fmean)}
        for index, values in enumerate(zip(*curves))], 'run_curves': curves, 'trajectories': trajectories, 'inputs': encoded,
        'tie_policy': PAIR_TIE_POLICY}


def write_report(report: ReportWithSimulation, output: Path) -> None:
    simulation = report['simulation']
    feedback: FrozenFeedback = {'inputs': simulation['inputs'], 'trajectories': simulation['trajectories']}
    compressed = gzip.compress(json.dumps(feedback, separators=(',', ':')).encode(), mtime=0)
    artifact = output.with_suffix('.feedback.json.gz')
    _ = artifact.write_bytes(compressed)
    saved: SavedSimulation = {'runs': simulation['runs'], 'seed': simulation['seed'], 'labels': simulation['labels'],
                              'curves': simulation['curves'], 'run_curves': simulation['run_curves'], 'tie_policy': simulation['tie_policy'],
                              'feedback_file': artifact.name, 'feedback_sha256': hashlib.sha256(compressed).hexdigest()}
    _ = output.write_text(json.dumps({**report, 'simulation': saved}, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def chronological_fixture(directory: Path) -> ReplayReport:
    store = ListingStore(directory / 'chronological.sqlite3')
    _ = store.update_settings(**SETTINGS)
    identifiers = fill(store, 24, 0)
    for index in (3, 8, 14, 21):
        _ = rerank(store, seed=1)
        store.feedback(identifiers[index], favorite=True)
    _ = rerank(store, seed=1)
    store.request_contact(identifiers[5])
    store.answer_pair(identifiers[6], identifiers[7])
    return store_report(store.path)['prequential']


class Arguments(argparse.Namespace):
    runs: int = 200
    labels: int = 40
    workers: int = 4
    output: Path = Path()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('--runs', type=int, default=200)
    _ = parser.add_argument('--labels', type=int, default=40)
    _ = parser.add_argument('--workers', type=int, default=4)
    _ = parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(namespace=Arguments())
    with tempfile.TemporaryDirectory(prefix='ranking-experiment-') as directory:
        store = ListingStore(Path(directory) / 'listings.sqlite3')
        _ = store.update_settings(**SETTINGS)
        _ = fill(store, 300, 50)
        seconds, _ = least_cpu_seconds(lambda: rerank(store, seed=1))
        prequential_fixture = chronological_fixture(Path(directory))
        measured = store_report(store.path)
        problem = inputs(garden_neighborhood())
        identifiers = [listing['id'] for listing in problem['listings']]
        garden_result = garden(problem, {'favorites': [identifiers[index] for index in GARDEN[:5]], 'alike': [identifiers[GARDEN[5]]]})
        sample = store.ranking_inputs()
        sample['listings'] = sample['listings'][:80]
        report: ExperimentReport = {'data': 'synthetic; no real store available', 'rerank_cpu_seconds': seconds,
                                    'prequential_fixture': prequential_fixture, 'store': measured, 'garden': garden_result,
                                    'simulation': parallel_simulation(sample, args.runs, args.labels, args.workers)}
        write_report(report, args.output)


if __name__ == '__main__':
    main()
