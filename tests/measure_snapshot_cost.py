"""Measure journal payload and lock duration on a disposable backup of a supplied store copy."""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/ranking/scripts'))

from measurement import bootstrap, read_copy
from learning import compress, encode


def sample_cost(store, listing, samples):
    reads, writes, sizes, raw_sizes, impressions = [], [], [], [], []
    for _ in range(samples):
        with store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            started = time.perf_counter()
            inputs = store.ranking_inputs(connection=connection)
            inputs['impressions'] = store.favorite_impressions(connection, listing, datetime.now(timezone.utc).isoformat())
            reads.append((time.perf_counter() - started) * 1000)
            raw_sizes.append(len(encode(inputs).encode('utf-8')))
            impressions.append(len(inputs['impressions']))
            started = time.perf_counter()
            store.record_learning_event(connection, 'favorite', listing)
            writes.append((time.perf_counter() - started) * 1000)
            sizes.append(connection.execute('SELECT length(inputs) FROM learning_events ORDER BY id DESC LIMIT 1').fetchone()[0])
            connection.rollback()
    return {'samples': samples, 'raw_bytes': median(raw_sizes), 'compressed_bytes': median(sizes),
            'compressed_bytes_interval': bootstrap(sizes), 'impression_rows': max(impressions),
            'read_ms': median(reads), 'read_interval': bootstrap(reads),
            'journal_write_ms': median(writes), 'journal_write_interval': bootstrap(writes)}


def measure(source, *, visits=100, samples=20):
    def run(store):
        inputs = store.ranking_inputs()
        listings = inputs['listings']
        if not listings:
            raise ValueError('Snapshot measurement needs at least one listing.')
        chosen = listings[-1]['id']
        original = sample_cost(store, chosen, samples)
        now = datetime.now(timezone.utc)
        rows = [(f'stress-{visit}-{rank}', f'stress-{visit}', listing['id'], listing['fingerprint'], 'impression',
                 (now - timedelta(seconds=visits - visit)).isoformat(), None, None, rank)
                for visit in range(visits) for rank, listing in enumerate(listings, 1)]
        with store.connect() as connection:
            connection.executemany('INSERT INTO implicit_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
        stressed = sample_cost(store, chosen, samples)
        with store.connect() as connection:
            unbounded = [dict(row) for row in connection.execute("SELECT listing_id, fingerprint, visit_id, shown_rank, occurred_at FROM implicit_signals WHERE kind = 'impression'")]
        legacy = encode({**inputs, 'impressions': unbounded})
        return {'listings': len(listings), 'original_store': original, 'stress': stressed, 'synthetic_impressions_added': len(rows),
                'all_history_snapshot_raw_bytes': len(legacy.encode('utf-8')),
                'all_history_snapshot_compressed_bytes': len(compress(legacy)),
                'method': 'Wall time inside an IMMEDIATE transaction; inserts rolled back; 95% bootstrap over samples. Stress impressions are synthetic and exist only in the disposable backup.'}
    return read_copy(source, run)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('store', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(measure(args.store), indent=2) + '\n')
