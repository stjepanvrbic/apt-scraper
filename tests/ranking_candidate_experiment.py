"""Evaluate grouped priors on previously recorded, identical simulated feedback."""
import argparse
import gzip
import hashlib
import json
import shlex
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/ranking/scripts'))

from measurement import bootstrap, pair_accuracy, record_simulated_pair, simulation_start, store_report, utilities
from learning import decode
from ranking_priors import tuned_candidate
from test_rerank import SETTINGS, fill, least_cpu_seconds
from store import ListingStore
from scoring import rank_listings
from review import review_inputs
from implicit_signals import diagnostic


def provenance(report, command, date_utc=None):
    identifier = hashlib.sha256(json.dumps(report, sort_keys=True, allow_nan=False).encode()).hexdigest()[:16]
    return {'id': identifier, 'date_utc': date_utc or datetime.now(timezone.utc).date().isoformat(),
            'command': command, 'timing_selection': 'Least CPU time of three complete reranks on 300 listings and 50 favorites.'}


def evaluate(arguments):
    encoded, trajectory = arguments
    state = simulation_start(decode(encoded))
    history = []
    for chosen, other in trajectory['pairs'][:20]:
        record_simulated_pair(state, chosen, other, history)
    state['history'] = history
    values = utilities(tuned_candidate(state))
    taste = trajectory['taste']
    return pair_accuracy(values, taste, trajectory['held_pairs'])


def candidate_rerank(store):
    inputs = store.ranking_inputs(include_signals=True)
    model = tuned_candidate(inputs)
    result = rank_listings(inputs, seed=1, model=model)
    result['check']['inputs'] = review_inputs(inputs, result)
    result['summary']['implicit_signals'] = diagnostic(inputs, result['summary'], model)
    store.save_rerank(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--store', type=Path, help='Optional working copy for actual-label replay; never the live profile database.')
    args = parser.parse_args()
    original = json.loads(args.baseline.read_text())['simulation']
    compressed = (args.baseline.parent / original['feedback_file']).read_bytes()
    if hashlib.sha256(compressed).hexdigest() != original['feedback_sha256']:
        raise ValueError('Feedback artifact checksum differs from the baseline report.')
    feedback = json.loads(gzip.decompress(compressed))
    baseline = [curve[4] for curve in original['run_curves']]
    with tempfile.TemporaryDirectory(prefix='ranking-priors-') as directory:
        store = ListingStore(Path(directory) / 'listings.sqlite3')
        store.update_settings(**SETTINGS)
        fill(store, 300, 50)
        seconds, _ = least_cpu_seconds(lambda: candidate_rerank(store))
    candidate = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        for accuracy in pool.map(evaluate, [(feedback['inputs'], trajectory) for trajectory in feedback['trajectories']]):
            candidate.append(accuracy)
            if len(candidate) % 10 == 0:
                print(f'{len(candidate)}/{len(baseline)} candidate fits complete', flush=True)
    improvement = [after - before for before, after in zip(baseline, candidate)]
    report = {'runs': len(candidate), 'labels': 20, 'age_assumption': 'one answer per day; stationary taste',
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
    args.output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
