"""Exercise a hypothesis lifecycle with a declared synthetic cue; no model proposal is claimed."""

import argparse
import hashlib
import json
import shlex
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median
from typing import NotRequired, TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/ranking/scripts'))

from test_rerank import SETTINGS, fill
from test_scoring import record_of
from hypotheses import Assessment, HypothesisTask, active, assess, pending_judgments, prepare, save, save_judgments
from measurement import Interval, bootstrap
from rerank import rerank
from store import ListingStore, select


class JudgmentBudget(TypedDict):
    """What judging the seeded hypotheses costs: text batches, listings read with photos, and photos per such listing."""
    text_batches: int
    visual_listing_reads: int
    full_resolution_photos_per_visual_listing: int


class RankSummary(TypedDict):
    """The held-out ranks of the labeled listings with and without the hypothesis column."""
    with_median: float
    with_interval: Interval | None
    without_median: float
    without_interval: Interval | None
    mean_improvement: float
    improvement_interval: Interval | None


class SummarizedAssessment(Assessment):
    rank_summary: NotRequired[RankSummary]


class PassStorage(TypedDict):
    count: int
    mean_bytes: float
    interval: Interval | None


class RerankBenchmark(TypedDict):
    listings: int
    favorites: int
    hypotheses: int
    cpu_seconds: list[float]
    least_cpu_seconds: float
    observed_range: list[float]
    bar_met: bool
    timing_selection: str


class Provenance(TypedDict):
    id: str
    date_utc: str
    command: str


class HypothesisReport(TypedDict):
    data: str
    new_labels: int
    requested_passes: int
    reranks_with_new_labels: int
    remote_model_calls: int
    budget: JudgmentBudget
    assessments: list[SummarizedAssessment]
    real_garden_proposal: str
    real_twenty_label_survival: str
    pass_storage: PassStorage
    promotion: str
    rerank_benchmark: NotRequired[RerankBenchmark]
    run: NotRequired[Provenance]


def requested(task: HypothesisTask | None) -> HypothesisTask:
    """The hypothesis pass the store requested after a rerank with new labels."""
    if task is None:
        raise ValueError('The store requested no hypothesis pass.')
    return task


def populate_benchmark(store: ListingStore) -> None:
    _ = store.update_settings(**SETTINGS)
    identifiers = fill(store, 300, 50)
    for round_number in range(2):
        _ = rerank(store, seed=1)
        task = requested(prepare(store))
        summary = record_of(next(item for item in task['records'] if item['id'] == identifiers[0]))['summary']
        proposals = [{'phrase': f'benchmark detail {round_number * 3 + number}', 'kind': 'textual',
                      'evidence': [{'id': identifiers[0], 'text': summary}]} for number in range(3)]
        _ = save(store, {'rerank': task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': proposals})
        if round_number == 0:
            store.answer_pair(identifiers[50], identifiers[51])
    fingerprints = {item['id']: item['fingerprint'] for item in store.ranking_inputs()['listings']}
    entries = [{'hypothesis': hypothesis['id'], 'id': identifier, 'fingerprint': fingerprints[identifier],
                'verdict': 'has' if index % (number + 2) == 0 else 'rules out', 'evidence': 'Fixture.'}
               for number, hypothesis in enumerate(active(store)) for index, identifier in enumerate(identifiers)]
    save_judgments(store, entries)


def seed_hypotheses(store: ListingStore, count: int = 60) -> tuple[list[str], JudgmentBudget]:
    _ = store.update_settings(**SETTINGS, judge=lambda criteria, features: 'outside')
    identifiers = fill(store, count, 0)
    with store.connect(write=True) as connection:
        for index, identifier in enumerate(identifiers):
            summary = 'Original wood trim throughout.' if index % 2 == 0 else 'Painted trim throughout.'
            _ = connection.execute("UPDATE feature_records SET extraction = json_set(extraction, '$.summary', ?) WHERE listing_id = ?", (summary, identifier))
    store.feedback(identifiers[0], favorite=True)
    store.feedback(identifiers[2], favorite=True)
    _ = rerank(store, seed=1)
    task = requested(prepare(store))
    proposals = [{'phrase': phrase, 'kind': kind, 'evidence': [{'id': identifiers[0], 'text': 'Original wood trim throughout.'}]}
                 for phrase, kind in [('original wood trim', 'textual'), ('hand painted tile', 'textual'), ('white kitchen cabinet doors', 'visual')]]
    _ = save(store, {'rerank': task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': proposals})
    planned = pending_judgments(store)
    entries: list[dict[str, str]] = []
    for hypothesis in active(store):
        if hypothesis['kind'] == 'visual':
            continue
        for index, identifier in enumerate(identifiers):
            entries.append({'hypothesis': hypothesis['id'], 'id': identifier, 'fingerprint': store.listing(identifier)['content_fingerprint'],
                            'verdict': 'has' if hypothesis['phrase'] == 'original wood trim' and index % 2 == 0 else 'rules out',
                            'evidence': 'Synthetic fixture truth.'})
    save_judgments(store, entries)
    return identifiers, {'text_batches': sum(not task['photos_required'] for task in planned),
                         'visual_listing_reads': sum(task['photos_required'] for task in planned), 'full_resolution_photos_per_visual_listing': 6}


def run(directory: Path) -> HypothesisReport:
    store = ListingStore(directory / 'listings.sqlite3')
    identifiers, budget = seed_hypotheses(store)
    requests = 1
    for identifier in identifiers[4:44:2]:
        store.feedback(identifier, favorite=True)
        _ = rerank(store, seed=1)
        task = prepare(store)
        requests += int(task is not None)
        if task:
            _ = save(store, {'rerank': task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': []})
    assessments: list[SummarizedAssessment] = [{**item} for item in assess(store)]
    with store.connect() as connection:
        tasks = [cast(str, row['task']) for row in select(connection, 'SELECT task FROM hypothesis_passes')]
    sizes = [len(task.encode('utf-8')) for task in tasks]
    report: HypothesisReport = {
        'data': 'synthetic fixture; scripted proposals and known judgments, not model output',
        'new_labels': 20, 'requested_passes': requests, 'reranks_with_new_labels': 21, 'remote_model_calls': 0,
        'budget': budget, 'assessments': assessments,
        'real_garden_proposal': 'Reported separately from this scripted fixture.',
        'real_twenty_label_survival': 'Reported separately from this scripted fixture.',
        'pass_storage': {'count': len(tasks), 'mean_bytes': fmean(sizes), 'interval': bootstrap(sizes, fmean)},
        'promotion': 'automatic hypothesis generation stays outside the installed review job'}
    for item in assessments:
        pairs = item['comparisons']['rank']
        if pairs:
            with_column, without = [a for a, _ in pairs], [b for _, b in pairs]
            improvement = [b - a for a, b in pairs]
            item['rank_summary'] = {'with_median': median(with_column), 'with_interval': bootstrap(with_column),
                                    'without_median': median(without), 'without_interval': bootstrap(without),
                                    'mean_improvement': fmean(improvement), 'improvement_interval': bootstrap(improvement, fmean)}
    return report


class Arguments(argparse.Namespace):
    output: Path = Path()
    benchmark: bool = False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('output', type=Path)
    _ = parser.add_argument('--benchmark', action='store_true')
    args = parser.parse_args(namespace=Arguments())
    with tempfile.TemporaryDirectory(prefix='ranking-hypotheses-') as directory:
        report = run(Path(directory))
        if args.benchmark:
            store = ListingStore(Path(directory) / 'benchmark.sqlite3')
            populate_benchmark(store)
            times: list[float] = []
            for _ in range(3):
                started = time.process_time()
                _ = rerank(store, seed=1)
                times.append(time.process_time() - started)
            report['rerank_benchmark'] = {'listings': 300, 'favorites': 50, 'hypotheses': 6,
                                          'cpu_seconds': times, 'least_cpu_seconds': min(times),
                                          'observed_range': [min(times), max(times)], 'bar_met': min(times) < 2,
                                          'timing_selection': 'Least of three complete reranks, including store read/write and leave-one-out.'}
    report['run'] = {'id': hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()[:16],
                     'date_utc': datetime.now(timezone.utc).date().isoformat(), 'command': shlex.join(['python', *sys.argv])}
    _ = args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
