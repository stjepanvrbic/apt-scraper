"""Measure a supplied store copy, keeping its frozen records beside the local report."""

import argparse
from pathlib import Path

from ranking_experiments import ReportWithSimulation, parallel_simulation, write_report
from measurement import StoreReport, read_copy, store_report
from learning import decode
from scoring import design


class RealStoreReport(ReportWithSimulation):
    """The report of simulated tastes over a store copy's real feature records."""
    data: str
    real_store: StoreReport
    eligible_feature_records: int


class Arguments(argparse.Namespace):
    store: Path = Path()
    output: Path = Path()
    runs: int = 200
    labels: int = 40


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('store', type=Path, help='A working copy, never the live profile database.')
    _ = parser.add_argument('output', type=Path, help='Local report path; its companion contains the source feature records.')
    _ = parser.add_argument('--runs', type=int, default=200)
    _ = parser.add_argument('--labels', type=int, default=40)
    args = parser.parse_args(namespace=Arguments())
    inputs = read_copy(args.store, lambda store: store.ranking_inputs())
    real_store = store_report(args.store)
    simulation = parallel_simulation(inputs, args.runs, args.labels, 4)
    report: RealStoreReport = {'data': 'simulated tastes over real feature records; not observed owner preferences',
                               'real_store': real_store, 'simulation': simulation,
                               'eligible_feature_records': len(design(decode(simulation['inputs'])).ids)}
    write_report(report, args.output)


if __name__ == '__main__':
    main()
