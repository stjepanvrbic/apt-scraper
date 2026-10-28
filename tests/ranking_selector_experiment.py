"""Compare prior-only cold-start questions with the installed variance selector."""

import argparse
import gzip
import hashlib
import json
import random
import shlex
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
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


def threshold_uncertainty(baseline, candidate):
    generator = random.Random(0)
    before, after, reductions = [], [], []
    for _ in range(2000):
        indices = generator.choices(range(len(baseline)), k=len(baseline))
        first, second = [crossing([fmean(values) for values in zip(*(curves[index] for index in indices))])
                         for curves in (baseline, candidate)]
        before.append(first)
        after.append(second)
        if first and second is not None:
            reductions.append(1 - second / first)
    def interval(values):
        ordered = sorted(values, key=lambda value: float('inf') if value is None else value)
        return [ordered[50], ordered[1949]]
    return {'baseline_interval': interval(before), 'candidate_interval': interval(after),
            'reduction_interval': interval(reductions) if len(reductions) == 2000 else None,
            'censored_draws': sum(a is None or b is None for a, b in zip(before, after)),
            'method': 'Paired bootstrap over users, 2000 resamples; null threshold bounds exceed 40 labels; reduction interval requires all draws identifiable.'}


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
              'threshold_uncertainty': threshold_uncertainty(original['run_curves'], curves),
              'label_reduction': reduction, 'bar': 0.25, 'bar_met': reduction is not None and reduction >= 0.25,
              'baseline_curves': original['curves'], 'candidate_curves': [
                  {'labels': index * 5, 'accuracy': fmean(values), 'interval': bootstrap(list(values), fmean)}
                  for index, values in enumerate(zip(*curves))],
              'paired_change_at_40': fmean([a[-1] - b[-1] for a, b in zip(curves, original['run_curves'])]),
              'paired_interval_at_40': bootstrap([a[-1] - b[-1] for a, b in zip(curves, original['run_curves'])], fmean),
              'censoring': 'A null threshold means not reached within 40 answers; no extrapolation.',
              'real_store': 'Actual-label replay is reported separately; this experiment uses simulated preferences.',
              'production': 'prior-only cold-start selector stays out; retain the variance selector',
              'stop_condition': 'not established: threshold is censored at 40' if reduction is None else 'fired' if reduction <= 0 else 'not fired',
              'candidate_runs': curves}
    report['run'] = {'id': hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()[:16],
                     'date_utc': datetime.now(timezone.utc).date().isoformat(), 'command': shlex.join(['python', *sys.argv]),
                     'feedback_sha256': original['feedback_sha256']}
    args.output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
