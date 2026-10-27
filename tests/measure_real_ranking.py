"""Measure a supplied store copy, keeping its frozen records beside the local report."""

import argparse
from pathlib import Path

from ranking_experiments import parallel_simulation, write_report
from measurement import read_copy, store_report
from learning import decode
from scoring import design


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('store', type=Path, help='A working copy, never the live profile database.')
    parser.add_argument('output', type=Path, help='Local report path; its companion contains the source feature records.')
    parser.add_argument('--runs', type=int, default=200)
    parser.add_argument('--labels', type=int, default=40)
    args = parser.parse_args()
    inputs = read_copy(args.store, lambda store: store.ranking_inputs())
    report = {'data': 'simulated tastes over real feature records; not observed owner preferences',
              'real_store': store_report(args.store), 'simulation': parallel_simulation(inputs, args.runs, args.labels, 4)}
    report['eligible_feature_records'] = len(design(decode(report['simulation']['inputs'])).ids)
    write_report(report, args.output)


if __name__ == '__main__':
    main()
