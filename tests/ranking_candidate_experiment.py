"""Evaluate grouped priors on previously recorded, identical simulated feedback."""
import argparse
import gzip
import hashlib
import json
import shlex
import sys
import tempfile
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import NotRequired, TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/ranking/scripts'))

from measurement import (HistoryInputs, Interval, ReplayEvent, StoreReport, Trajectory, bootstrap, pair_accuracy, record_simulated_pair,
                         simulation_start, store_report, utilities)
from learning import decode
from ranking_experiments import FrozenFeedback, SavedReport
from ranking_priors import tuned_candidate
from test_rerank import SETTINGS, fill, least_cpu_seconds
from store import ListingStore
from scoring import RankingResult, rank_listings
from review import ReviewSnapshot, review_inputs
from implicit_signals import diagnostic
from shapes import loads


class Provenance(TypedDict):
    """What identifies a report's numbers: a digest of them, the day and the command that produced them."""
    id: str
    date_utc: str
    command: str
    timing_selection: str


class RealStoreComparison(TypedDict):
    baseline: StoreReport
    candidate: StoreReport


class CandidateReport(TypedDict):
    runs: int
    labels: int
    age_assumption: str
    baseline_accuracy: float
    baseline_interval: Interval | None
    candidate_accuracy: float
    candidate_interval: Interval | None
    improvement: float
    improvement_interval: Interval | None
    bar: float
    simulation_bar_met: bool
    rerank_cpu_seconds: float
    cpu_bar_met: bool
    real_store_check: str
    production: str
    stop_condition_fired: bool
    baseline_runs: list[float]
    candidate_runs: list[float]
    real_store: NotRequired[RealStoreComparison]
    run: NotRequired[Provenance]


def provenance(report: Mapping[str, object], command: str, date_utc: str | None = None) -> Provenance:
    identifier = hashlib.sha256(json.dumps(report, sort_keys=True, allow_nan=False).encode()).hexdigest()[:16]
    return {'id': identifier, 'date_utc': date_utc or datetime.now(timezone.utc).date().isoformat(),
            'command': command, 'timing_selection': 'Least CPU time of three complete reranks on 300 listings and 50 favorites.'}


def evaluate(arguments: tuple[str, Trajectory]) -> float:
    encoded, trajectory = arguments
    state = simulation_start(decode(encoded))
    history: list[ReplayEvent] = []
    for chosen, other in trajectory['pairs'][:20]:
        record_simulated_pair(state, chosen, other, history)
    context: HistoryInputs = {**state, 'history': history}
    values = utilities(tuned_candidate(context))
    taste = trajectory['taste']
    return pair_accuracy(values, taste, trajectory['held_pairs'])


def candidate_rerank(store: ListingStore) -> RankingResult:
    inputs = store.ranking_inputs(include_signals=True)
    model = tuned_candidate(inputs)
    result = rank_listings(inputs, seed=1, model=model)
    snapshot: ReviewSnapshot = {**result['check'], 'inputs': review_inputs(inputs, result)}
    result['check'] = snapshot
    result['summary']['implicit_signals'] = diagnostic(inputs, result['summary'], model)
    _ = store.save_rerank(result)
    return result


class Arguments(argparse.Namespace):
    baseline: Path = Path()
    output: Path = Path()
    store: Path | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('baseline', type=Path)
    _ = parser.add_argument('output', type=Path)
    _ = parser.add_argument('--store', type=Path, help='Optional working copy for actual-label replay; never the live profile database.')
    args = parser.parse_args(namespace=Arguments())
    original = cast(SavedReport, loads(args.baseline.read_text()))['simulation']
    compressed = (args.baseline.parent / original['feedback_file']).read_bytes()
    if hashlib.sha256(compressed).hexdigest() != original['feedback_sha256']:
        raise ValueError('Feedback artifact checksum differs from the baseline report.')
    feedback = cast(FrozenFeedback, loads(gzip.decompress(compressed)))
    baseline = [curve[4] for curve in original['run_curves']]
    with tempfile.TemporaryDirectory(prefix='ranking-priors-') as directory:
        store = ListingStore(Path(directory) / 'listings.sqlite3')
        _ = store.update_settings(**SETTINGS)
        _ = fill(store, 300, 50)
        seconds, _ = least_cpu_seconds(lambda: candidate_rerank(store))
    candidate: list[float] = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        for accuracy in pool.map(evaluate, [(feedback['inputs'], trajectory) for trajectory in feedback['trajectories']]):
            candidate.append(accuracy)
            if len(candidate) % 10 == 0:
                print(f'{len(candidate)}/{len(baseline)} candidate fits complete', flush=True)
    improvement = [after - before for before, after in zip(baseline, candidate)]
    report: CandidateReport = {'runs': len(candidate), 'labels': 20, 'age_assumption': 'one answer per day; stationary taste',
                               'baseline_accuracy': fmean(baseline), 'baseline_interval': bootstrap(baseline, fmean),
                               'candidate_accuracy': fmean(candidate), 'candidate_interval': bootstrap(candidate, fmean),
                               'improvement': fmean(improvement), 'improvement_interval': bootstrap(improvement, fmean),
                               'bar': 0.05, 'simulation_bar_met': fmean(improvement) >= 0.05,
                               'rerank_cpu_seconds': seconds, 'cpu_bar_met': seconds < 2,
                               'real_store_check': 'Actual-label replay is separate from the simulated-user comparison.',
                               'production': 'priors stay as on main', 'stop_condition_fired': fmean(improvement) <= 0,
                               'baseline_runs': baseline, 'candidate_runs': candidate}
    if args.store:
        report['real_store'] = {'baseline': store_report(args.store), 'candidate': store_report(args.store, fitter=tuned_candidate)}
    report['run'] = provenance(report, shlex.join(['python', *sys.argv]))
    _ = args.output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
