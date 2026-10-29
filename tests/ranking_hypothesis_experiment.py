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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/ranking/scripts'))

from test_rerank import SETTINGS, fill
from hypotheses import active, assess, pending_judgments, prepare, save, save_judgments
from measurement import bootstrap
from rerank import rerank
from store import ListingStore


def populate_benchmark(store):
    store.update_settings(**SETTINGS)
    identifiers = fill(store, 300, 50)
    for round_number in range(2):
        rerank(store, seed=1)
        task = prepare(store)
        summary = next(item['record']['summary'] for item in task['records'] if item['id'] == identifiers[0])
        proposals = [{'phrase': f'benchmark detail {round_number * 3 + number}', 'kind': 'textual',
                      'evidence': [{'id': identifiers[0], 'text': summary}]} for number in range(3)]
        save(store, {'rerank': task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': proposals})
        if round_number == 0:
            store.answer_pair(identifiers[50], identifiers[51])
    fingerprints = {item['id']: item['fingerprint'] for item in store.ranking_inputs()['listings']}
    entries = [{'hypothesis': hypothesis['id'], 'id': identifier, 'fingerprint': fingerprints[identifier],
                'verdict': 'has' if index % (number + 2) == 0 else 'rules out', 'evidence': 'Fixture.'}
               for number, hypothesis in enumerate(active(store)) for index, identifier in enumerate(identifiers)]
    save_judgments(store, entries)


def seed_hypotheses(store, count=60):
    store.update_settings(**SETTINGS, judge=lambda criteria, features: 'outside')
    identifiers = fill(store, count, 0)
    with store.connect(write=True) as connection:
        for index, identifier in enumerate(identifiers):
            summary = 'Original wood trim throughout.' if index % 2 == 0 else 'Painted trim throughout.'
            connection.execute("UPDATE feature_records SET extraction = json_set(extraction, '$.summary', ?) WHERE listing_id = ?", (summary, identifier))
    store.feedback(identifiers[0], favorite=True)
    store.feedback(identifiers[2], favorite=True)
    rerank(store, seed=1)
    task = prepare(store)
    proposals = [{'phrase': phrase, 'kind': kind, 'evidence': [{'id': identifiers[0], 'text': 'Original wood trim throughout.'}]}
                 for phrase, kind in [('original wood trim', 'textual'), ('hand painted tile', 'textual'), ('white kitchen cabinet doors', 'visual')]]
    save(store, {'rerank': task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': proposals})
    planned = pending_judgments(store)
    entries = []
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


def run(directory):
    store = ListingStore(directory / 'listings.sqlite3')
    identifiers, budget = seed_hypotheses(store)
    requests = 1
    for identifier in identifiers[4:44:2]:
        store.feedback(identifier, favorite=True)
        rerank(store, seed=1)
        task = prepare(store)
        requests += int(task is not None)
        if task:
            save(store, {'rerank': task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': []})
    assessments = assess(store)
    with store.connect() as connection:
        tasks = [row['task'] for row in connection.execute('SELECT task FROM hypothesis_passes')]
    sizes = [len(task.encode('utf-8')) for task in tasks]
    report = {'data': 'synthetic fixture; scripted proposals and known judgments, not model output',
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--benchmark', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='ranking-hypotheses-') as directory:
        report = run(Path(directory))
        if args.benchmark:
            store = ListingStore(Path(directory) / 'benchmark.sqlite3')
            populate_benchmark(store)
            times = []
            for _ in range(3):
                started = time.process_time()
                rerank(store, seed=1)
                times.append(time.process_time() - started)
            report['rerank_benchmark'] = {'listings': 300, 'favorites': 50, 'hypotheses': 6,
                                          'cpu_seconds': times, 'least_cpu_seconds': min(times),
                                          'observed_range': [min(times), max(times)], 'bar_met': min(times) < 2,
                                          'timing_selection': 'Least of three complete reranks, including store read/write and leave-one-out.'}
    report['run'] = {'id': hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()[:16],
                     'date_utc': datetime.now(timezone.utc).date().isoformat(), 'command': shlex.join(['python', *sys.argv])}
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
