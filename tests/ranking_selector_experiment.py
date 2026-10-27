"""Compare prior-only cold-start questions with the installed variance selector."""

import argparse
import gzip
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import fmean

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/ranking/scripts'))

from measurement import bootstrap, simulate
from learning import decode
from ranking_selectors import cold_questions


def batch(arguments):
    encoded, seed, runs = arguments
    return simulate(decode(encoded), cold_questions, runs=runs, labels=40, seed=seed)['run_curves']


def crossing(curve):
    return next((index * 5 for index, value in enumerate(curve) if value >= 0.75), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    original = json.loads(args.baseline.read_text())['simulation']
    compressed = (args.baseline.parent / original['feedback_file']).read_bytes()
    if hashlib.sha256(compressed).hexdigest() != original['feedback_sha256']:
        raise ValueError('Feedback checksum mismatch.')
    feedback = json.loads(gzip.decompress(compressed))
    curves = []
    runs = original['runs']
    with ProcessPoolExecutor(max_workers=4) as pool:
        for result in pool.map(batch, [(feedback['inputs'], seed, min(10, runs - seed)) for seed in range(0, runs, 10)]):
            curves.extend(result)
            print(f'{len(curves)}/{runs} cold-start users complete', flush=True)
    baseline = [point['accuracy'] for point in original['curves']]
    candidate = [fmean(values) for values in zip(*curves)]
    before, after = crossing(baseline), crossing(candidate)
    reduction = 1 - after / before if before and after is not None else None
    report = {'runs': runs, 'horizon': 40, 'baseline_labels_to_0.75': before, 'candidate_labels_to_0.75': after,
              'label_reduction': reduction, 'bar': 0.25, 'bar_met': reduction is not None and reduction >= 0.25,
              'baseline_curves': original['curves'], 'candidate_curves': [
                  {'labels': index * 5, 'accuracy': fmean(values), 'interval': bootstrap(list(values), fmean)}
                  for index, values in enumerate(zip(*curves))],
              'paired_change_at_40': fmean([a[-1] - b[-1] for a, b in zip(curves, original['run_curves'])]),
              'paired_interval_at_40': bootstrap([a[-1] - b[-1] for a, b in zip(curves, original['run_curves'])], fmean),
              'censoring': 'A null threshold means not reached within 40 answers; no extrapolation.',
              'real_store': 'not run, no database in the authorized source directory',
              'production': 'prior-only cold-start selector stays out; retain the variance selector',
              'stop_condition': 'not established: both label thresholds are right-censored at 40', 'candidate_runs': curves}
    args.output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
