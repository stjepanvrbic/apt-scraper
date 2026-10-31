"""Re-score frozen simulated feedback without repeating query selection."""

import argparse
import copy
import gzip
import hashlib
import math
import json
import sys
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import fmean
from typing import TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/ranking/scripts'))

from measurement import PAIR_TIE_POLICY, Trajectory, bootstrap, pair_accuracy, utilities
from learning import decode
from ranking_experiments import FrozenFeedback, SavedReport, SavedSimulation
from scoring import fit_model
from shapes import loads


class RescoredSimulation(SavedSimulation):
    """A saved simulation after rescoring: the held-out pairs whose latent tastes tie, which score chance."""
    latent_tied_pairs: int


class RescoredReport(TypedDict):
    data: str
    simulation: RescoredSimulation


def validate_saved_curves(simulation: SavedSimulation, trajectories: Sequence[Trajectory]) -> list[int]:
    checkpoints = [point['labels'] for point in simulation['curves']]
    runs = simulation['runs']
    if type(runs) is not int or runs < 1 or len(trajectories) != runs or len(simulation['run_curves']) != runs:
        raise ValueError('Saved run counts do not match the frozen trajectories.')
    if not checkpoints or any(type(count) is not int or count < 0 for count in checkpoints) or checkpoints != sorted(set(checkpoints)):
        raise ValueError('Checkpoints must be a nonempty increasing sequence of label counts.')
    for curve, trajectory in zip(simulation['run_curves'], trajectories, strict=True):
        if len(curve) != len(checkpoints) or len(trajectory['pairs']) < checkpoints[-1]:
            raise ValueError('A saved curve or feedback trajectory is truncated.')
        if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for value in curve):
            raise ValueError('Saved accuracies must be finite numbers from zero to one.')
    return checkpoints


def score(arguments: tuple[str, Trajectory, Sequence[int]]) -> tuple[list[float], list[float]]:
    encoded, trajectory, checkpoints = arguments
    initial = decode(encoded)
    corrected: list[float] = []
    previous: list[float] = []
    for count in checkpoints:
        state = copy.deepcopy(initial)
        state['pairs'] = trajectory['pairs'][:count]
        paired = {identifier for pair in state['pairs'] for identifier in pair}
        for listing in state['listings']:
            listing['paired'] = listing['id'] in paired
        values = utilities(fit_model(state))
        taste, pairs = trajectory['taste'], trajectory['held_pairs']
        corrected.append(pair_accuracy(values, taste, pairs))
        previous.append(fmean(0.5 if values[a] == values[b] else (values[a] > values[b]) == (taste[a] > taste[b]) for a, b in pairs))
    return corrected, previous


class Arguments(argparse.Namespace):
    report: Path = Path()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('report', type=Path)
    args = parser.parse_args(namespace=Arguments())
    report = cast(SavedReport, loads(args.report.read_text(encoding='utf-8')))
    simulation = report['simulation']
    compressed = (args.report.parent / simulation['feedback_file']).read_bytes()
    if hashlib.sha256(compressed).hexdigest() != simulation['feedback_sha256']:
        raise ValueError('Frozen feedback checksum mismatch.')
    feedback = cast(FrozenFeedback, loads(gzip.decompress(compressed)))
    checkpoints = validate_saved_curves(simulation, feedback['trajectories'])
    curves: list[list[float]] = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        for index, (corrected, previous) in enumerate(pool.map(score, [
            (feedback['inputs'], trajectory, checkpoints) for trajectory in feedback['trajectories']])):
            reference = corrected if simulation.get('tie_policy') == PAIR_TIE_POLICY else previous
            if any(abs(a - b) > 1e-12 for a, b in zip(reference, simulation['run_curves'][index], strict=True)):
                raise ValueError(f'The frozen trajectory does not reproduce the earlier scores for run {index}.')
            curves.append(corrected)
            if len(curves) % 20 == 0:
                print(f'{len(curves)}/{simulation["runs"]} trajectories rescored', flush=True)
    rescored: RescoredSimulation = {
        **simulation, 'run_curves': curves,
        'curves': [{'labels': count, 'accuracy': fmean(values), 'interval': bootstrap(list(values), fmean)}
                   for count, values in zip(checkpoints, zip(*curves))],
        'latent_tied_pairs': sum(trajectory['taste'][a] == trajectory['taste'][b]
                                 for trajectory in feedback['trajectories'] for a, b in trajectory['held_pairs']),
        'tie_policy': PAIR_TIE_POLICY}
    result: RescoredReport = {**report, 'simulation': rescored, 'data': 'synthetic fixture; real-store evidence is reported separately'}
    _ = args.report.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
